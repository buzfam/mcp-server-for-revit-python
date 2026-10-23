# -*- coding: utf-8 -*-
"""
Files of the ribbon button MCP > Conferir > Auditar template: the audit report
(revit_mcp/audit.py build_audit) as an Excel workbook (.xlsx) and as a
self-contained HTML page, both in pt-BR.

The .xlsx is written by hand (OOXML parts with inline strings, zipped with the
stdlib zipfile), so no external library is needed. Pure module (no Revit or
pyRevit import): runs under CPython tests and IronPython 2.7
(scripts/ironpython_check.py). IronPython's json.dumps fails on accented text,
so nothing here goes through json.
"""

import datetime
import io
import os
import zipfile

from . import ribbon_reports as reports

try:
    import zlib  # noqa: F401 - only to know whether ZIP_DEFLATED works
    COMPRESSION = zipfile.ZIP_DEFLATED
except ImportError:
    COMPRESSION = zipfile.ZIP_STORED

try:
    TEXT = unicode  # noqa: F821 - IronPython 2.7
    INTEGERS = (int, long)  # noqa: F821
except NameError:  # CPython
    TEXT = str
    INTEGERS = (int,)

text = reports.text

MAX_SHEET_NAME = 31
MAX_CELL_TEXT = 32767
MAX_COLUMN_WIDTH = 60
BAD_SHEET_CHARS = u"[]:*?/\\"
YES, NO = u"Sim", u"Não"
UNUSED, USED = u"Sem uso", u"Em uso"

# Sheet name of each part of a section with several lists, in report order.
PART_SHEETS = (
    ("levels", u"Níveis"), ("grids", u"Eixos"), ("phases", u"Fases"),
    ("phase_filters", u"Filtros de fase"), ("styles", u"Estilos de linha"),
    ("line_patterns", u"Padrões de linha"), ("fill_patterns", u"Padrões de preenchimento"),
    ("dimension_types", u"Tipos de cota"), ("text_types", u"Tipos de texto"),
    ("tag_and_symbol_types", u"Etiquetas e símbolos"), ("sheets", u"Folhas"),
    ("title_blocks", u"Carimbos"), ("dwg", u"Exportação DWG"), ("pdf", u"Exportação PDF"),
    ("print_settings", u"Configurações de impressão"),
    ("view_sheet_sets", u"Conjuntos de folhas"),
)
PART_TITLES = dict(PART_SHEETS)
PART_RANK = dict((key, index) for index, (key, _) in enumerate(PART_SHEETS))
# Excel reserves this sheet name.
RESERVED_SHEET = u"history"
# Item fields -> column titles, in column order. "unused" becomes Situação and
# "id" is always the last column.
FIELDS = (
    ("number", u"Número"), ("name", u"Nome"), ("category", u"Categoria"),
    ("kind", u"Tipo de filtro"), ("type", u"Aplica-se a"), ("style", u"Estilo"),
    ("view_type", u"Tipo de vista"), ("class", u"Classe"),
    ("scale", u"Escala"), ("template", u"Modelo de vista"), ("order", u"Ordem"),
    ("elevation_m", u"Elevação (m)"), ("plan_views", u"Vistas de planta"),
    ("instances", u"Instâncias"), ("types", u"Tipos"), ("unused_types", u"Tipos sem uso"),
    ("in_place", u"Família no local"), ("views", u"Vistas"),
    ("templates", u"Em modelos de vista"), ("views_and_templates", u"Vistas e modelos"),
    ("filters", u"Filtros"), ("lines", u"Linhas desenhadas"), ("weight", u"Espessura"),
    ("color", u"Cor"), ("pattern", u"Padrão de linha"), ("builtin", u"Do Revit"),
    ("target", u"Uso do padrão"), ("sheets", u"Folhas"), ("title_block", u"Carimbo"),
    ("placeholder", u"Folha reservada"), ("on_sheet", u"Em folha"),
    ("key_schedule", u"Tabela-chave"), ("current", u"Em uso no navegador"),
    ("folders", u"Agrupamento"), ("shared", u"Compartilhado"), ("bound", u"Vinculado ao projeto"),
    ("binding", u"Vínculo"), ("categories_count", u"Nº de categorias"),
    ("categories", u"Categorias"), ("guid", u"GUID"), ("presentation", u"Apresentação"),
)
FIELD_TITLES = dict(FIELDS)
FIELD_ORDER = dict((key, index) for index, (key, _) in enumerate(FIELDS))
VALUE_LABELS = {
    "view_type": reports.VIEW_TYPES,
    "kind": {"parameter": u"Por regra", "selection": u"Seleção"},
    "type": {"Views": u"Vistas", "Sheets": u"Folhas", "Schedules": u"Tabelas"},
    "style": {"Linear": u"Linear", "Angular": u"Angular", "Radial": u"Radial",
              "Diameter": u"Diâmetro", "ArcLength": u"Comprimento de arco",
              "SpotElevation": u"Cota de elevação", "SpotCoordinate": u"Cota de coordenada",
              "SpotSlope": u"Cota de inclinação", "LinearFixed": u"Linear fixa",
              "AlignmentStationLabel": u"Estaca"},
    "binding": {"instance": u"Instância", "type": u"Tipo"},
    "target": {"Drafting": u"Desenho", "Model": u"Modelo"},
}
COVERAGE = {"api": u"automática", "parcial": u"parcial"}
FILE_TYPES = {True: u".rte (template)", False: u".rvt (projeto)"}
UNUSED_SOURCES = {"revit_purge": u"lista do Limpar não utilizados do Revit",
                  "instance_count": u"contagem de instâncias colocadas"}
# Revit API classes in the purge preview -> pt-BR (unknown classes stay as they are).
ELEMENT_KINDS = {
    "FamilySymbol": u"Tipo de família", "Family": u"Família", "Material": u"Material",
    "AppearanceAssetElement": u"Ativo de aparência", "PropertySetElement": u"Ativo físico/térmico",
    "LinePatternElement": u"Padrão de linha", "FillPatternElement": u"Padrão de preenchimento",
    "GraphicsStyle": u"Estilo gráfico", "View": u"Vista", "ViewFamilyType": u"Tipo de vista",
    "WallType": u"Tipo de parede", "FloorType": u"Tipo de piso", "RoofType": u"Tipo de telhado",
    "CeilingType": u"Tipo de forro", "MullionType": u"Tipo de montante",
    "PanelType": u"Tipo de painel", "CurtainSystemType": u"Tipo de sistema de cortina",
    "StairsType": u"Tipo de escada", "RailingType": u"Tipo de guarda-corpo",
    "DimensionType": u"Tipo de cota", "SpotDimensionType": u"Tipo de cota de ponto",
    "TextNoteType": u"Tipo de texto", "GroupType": u"Tipo de grupo",
    "ParameterFilterElement": u"Filtro", "SelectionFilterElement": u"Filtro de seleção",
    "PhaseFilter": u"Filtro de fase", "ImageType": u"Imagem", "CADLinkType": u"Vínculo CAD",
    "RevitLinkType": u"Vínculo do Revit", "ElementType": u"Tipo",
    "FilledRegionType": u"Tipo de região preenchida", "LevelType": u"Tipo de nível",
    "GridType": u"Tipo de eixo", "ViewSheet": u"Folha", "ViewSchedule": u"Tabela",
}
PURGE_EXTRA = (("view_templates", u"Modelo de vista (fora do Limpar do Revit)"),
               ("filters", u"Filtro (fora do Limpar do Revit)"))


# ---- values ----

def is_number(value):
    return isinstance(value, INTEGERS + (float,)) and not isinstance(value, bool)


def flat(value):
    """Any item value as one line of text (lists and dicts flattened)."""
    if value is None:
        return u""
    if isinstance(value, bool):
        return YES if value else NO
    if isinstance(value, dict):
        return u"; ".join(u"{}: {}".format(text(k), flat(value[k])) for k in sorted(value))
    if isinstance(value, (list, tuple)):
        return u"; ".join(flat(v) for v in value)
    return text(value)


def field_value(key, value):
    """A cell value: numbers stay numbers, the rest becomes pt-BR text."""
    if is_number(value):
        return value
    labels = VALUE_LABELS.get(key)
    if labels is not None and isinstance(value, reports.STRINGS) and value in labels:
        return labels[value]
    return flat(value)


def situation(value):
    if value is None:
        return u""
    return UNUSED if value else USED


def _is_summary(value):
    return isinstance(value, dict) and "count" in value and "items" in value


def section_lists(sections):
    """[(sheet title, section key, part key or None, summary)] in report order."""
    result = []
    for key, label in reports.AUDIT_SECTIONS:
        value = sections.get(key)
        if key == "naming" or value is None:
            continue
        if _is_summary(value):
            result.append((label, key, None, value))
        elif isinstance(value, dict):
            parts = sorted(value, key=lambda part: (PART_RANK.get(part, len(PART_RANK)), part))
            for part in parts:
                if _is_summary(value[part]):
                    title = PART_TITLES.get(part) or u"{} - {}".format(label, part)
                    result.append((title, key, part, value[part]))
    return result


def item_table(summary):
    """(columns, rows) of one list: one row per item."""
    items = [i for i in summary.get("items") or [] if isinstance(i, dict)]
    keys = set()
    for item in items:
        keys.update(item)
    tracked = summary.get("unused_count") is not None and "unused" in keys
    fields = sorted((k for k in keys if k not in ("id", "unused")),
                    key=lambda k: (FIELD_ORDER.get(k, len(FIELDS)), k))
    columns = []
    for key in fields:
        title = FIELD_TITLES.get(key, text(key))
        columns.append(title if title not in columns else u"{} ({})".format(title, key))
    if tracked:
        columns.append(u"Situação")
    columns.append(u"Id")
    rows = []
    for item in items:
        row = [field_value(k, item.get(k)) for k in fields]
        if tracked:
            row.append(situation(item.get("unused")))
        row.append(item.get("id"))
        rows.append(row)
    return columns, rows


# ---- sheet names ----

def sheet_name(title, used):
    """Valid, unique Excel sheet name (<= 31 chars, no []:*?/\\); adds it to used."""
    clean = u"".join(u" " if c in BAD_SHEET_CHARS else c for c in text(title))
    clean = u" ".join(clean.split()).strip(u"'") or u"Planilha"
    if clean.lower() == RESERVED_SHEET:
        clean = u"{} (2)".format(clean)

    def fit(suffix):
        # Slicing may leave a trailing apostrophe or space, which Excel rejects.
        return clean[:MAX_SHEET_NAME - len(suffix)].rstrip().rstrip(u"'") + suffix

    name = fit(u"")
    number = 2
    while name.lower() in used:
        name = fit(u" ({})".format(number))
        number += 1
    used.add(name.lower())
    return name


# ---- workbook content ----

def _today(today):
    return text(today) if today else datetime.date.today().isoformat()


def summary_sheet(report, lists, today):
    """Rows of the Resumo sheet (and which rows are headers)."""
    sections = report.get("sections") or {}
    rows, bold = [], set()

    def add(row, header=False):
        if header:
            bold.add(len(rows))
        rows.append(row)

    add([u"Auditoria do template"], header=True)
    add([u"Modelo", text(report.get("document"))])
    add([u"Data", _today(today)])
    add([u"Versão do Revit", text(report.get("revit_version"))])
    add([u"Tipo de arquivo", FILE_TYPES[bool(report.get("is_template_file"))]])
    if report.get("limit") is not None:
        add([u"Itens por lista (máximo)", report.get("limit")])
    source = report.get("unused_source")
    if source:
        add([u"Como \"sem uso\" foi decidido", UNUSED_SOURCES.get(source, text(source))])
    if report.get("unused_source_note"):
        add([u"Observação", text(report.get("unused_source_note"))])
    add([])
    add([u"Seção", u"Itens", u"Sem uso", u"Na planilha", u"Observação"], header=True)
    for title, _, _, summary in lists:
        unused = summary.get("unused_count")
        listed = len(summary.get("items") or [])
        notes = []
        if summary.get("truncated"):
            notes.append(u"lista cortada: {} de {}".format(listed, summary.get("count", 0)))
        if summary.get("unused_rule"):
            notes.append(u"sem uso = {}".format(text(summary.get("unused_rule"))))
        add([title, summary.get("count", 0), u"-" if unused is None else unused, listed,
             u"; ".join(notes)])
    purge = sections.get("purge_preview")
    if purge:
        add([u"Limpeza (prévia)", purge.get("count", 0), u"-",
             len(purge.get("items") or []), u"nada foi apagado"])
    naming = sections.get("naming") or {}
    if naming:
        add([])
        add([u"Nomenclatura", u"Conferidos", u"Fora do padrão", u"Padrão"], header=True)
        for kind in sorted(naming):
            check = naming[kind]
            add([text(kind), check.get("checked", 0), check.get("violations_count", 0),
                 text(check.get("pattern"))])
    errors = report.get("section_errors") or {}
    if errors:
        labels = dict(reports.AUDIT_SECTIONS)
        add([])
        add([u"Seção com erro ou pulada", u"Motivo"], header=True)
        for key in sorted(errors):
            add([labels.get(key, text(key)), text(errors[key])])
    budget = report.get("time_budget")
    if budget:
        add([])
        add([u"Tempo esgotado",
             u"passou de {} s; seções puladas: {}. Rode de novo só com elas.".format(
                 text(budget.get("seconds")),
                 u", ".join(text(s) for s in budget.get("skipped_sections") or []))])
    return rows, bold


def checklist_table(report):
    labels = dict(reports.AUDIT_SECTIONS)
    rows = []
    for item in report.get("checklist") or []:
        rows.append([item.get("item"), text(item.get("title")),
                     COVERAGE.get(item.get("coverage"), text(item.get("coverage"))),
                     u", ".join(text(labels.get(s, s)) for s in item.get("sections") or []),
                     text(item.get("check_manually"))])
    return [u"Item", u"Assunto", u"Status", u"Seções", u"Detalhe (conferir a olho)"], rows


def element_kind(kind):
    """Revit API class name -> pt-BR label (unknown names unchanged)."""
    return ELEMENT_KINDS.get(text(kind), text(kind))


def purge_table(purge):
    rows = []
    for item in purge.get("items") or []:
        rows.append([element_kind(item.get("kind")), text(item.get("category")),
                     text(item.get("name")), text(item.get("kind")), item.get("id")])
    extra = purge.get("not_in_revit_purge") or {}
    for key, label in PURGE_EXTRA:
        for item in (extra.get(key) or {}).get("items") or []:
            rows.append([label, u"", text(item.get("name")), u"", item.get("id")])
    return [u"Tipo de elemento", u"Categoria", u"Nome", u"Classe (API)", u"Id"], rows


def naming_table(naming):
    rows = []
    for kind in sorted(naming):
        check = naming[kind]
        for violation in check.get("violations") or []:
            rows.append([text(kind), text(check.get("pattern")), text(violation.get("name")),
                         violation.get("id")])
    return [u"Tipo de nome", u"Padrão", u"Nome fora do padrão", u"Id"], rows


def workbook_sheets(report, today=None):
    """[(sheet name, rows, bold row indexes, is_table)] of the workbook."""
    sections = report.get("sections") or {}
    lists = section_lists(sections)
    used = set()
    rows, bold = summary_sheet(report, lists, today)
    sheets = [(sheet_name(u"Resumo", used), rows, bold, False)]
    columns, rows = checklist_table(report)
    if rows:
        sheets.append((sheet_name(u"Checklist", used), [columns] + rows, set([0]), True))
    for title, _, _, summary in lists:
        columns, rows = item_table(summary)
        if rows:
            sheets.append((sheet_name(title, used), [columns] + rows, set([0]), True))
    naming = sections.get("naming") or {}
    columns, rows = naming_table(naming)
    if rows:
        sheets.append((sheet_name(u"Nomenclatura", used), [columns] + rows, set([0]), True))
    purge = sections.get("purge_preview")
    if purge:
        columns, rows = purge_table(purge)
        if rows:
            sheets.append((sheet_name(u"Limpeza (prévia)", used), [columns] + rows,
                           set([0]), True))
    return sheets


# ---- XML ----

def clean_xml(value):
    """Text without characters XML 1.0 forbids (control chars, lone surrogates)."""
    value = text(value)
    out = []
    index, size = 0, len(value)
    while index < size:
        char = value[index]
        code = ord(char)
        if 0xD800 <= code <= 0xDBFF and index + 1 < size and \
                0xDC00 <= ord(value[index + 1]) <= 0xDFFF:
            out.append(char)
            out.append(value[index + 1])  # surrogate pair (IronPython UTF-16)
            index += 2
            continue
        if code in (0x9, 0xA, 0xD) or (0x20 <= code <= 0xD7FF) or (0xE000 <= code <= 0xFFFD) \
                or code > 0xFFFF:
            out.append(char)
        index += 1
    return u"".join(out)


def escape(value):
    return (clean_xml(value).replace(u"&", u"&amp;").replace(u"<", u"&lt;")
            .replace(u">", u"&gt;").replace(u"\"", u"&quot;").replace(u"'", u"&apos;"))


def column_letter(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = u""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = u"ABCDEFGHIJKLMNOPQRSTUVWXYZ"[rest] + letters
    return letters


def number_text(value):
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return TEXT(repr(value))
    return u"{}".format(value)


def cell_xml(reference, value, bold):
    style = u' s="1"' if bold else u""
    if is_number(value):
        number = number_text(value)
        if number is not None:
            return u'<c r="{}"{}><v>{}</v></c>'.format(reference, style, number)
    # NaN and infinity are not valid numbers in a cell: written as text.
    value = (flat(value) if is_number(value) else field_value(None, value))[:MAX_CELL_TEXT]
    if not value:
        return u""
    return u'<c r="{}"{} t="inlineStr"><is><t xml:space="preserve">{}</t></is></c>'.format(
        reference, style, escape(value))


def _width(value):
    if is_number(value):
        return len(u"{}".format(value))
    return max([len(line) for line in field_value(None, value).split(u"\n")] or [0])


def sheet_xml(rows, bold, is_table, selected):
    width = max([len(r) for r in rows] or [1])
    widths = [8] * width
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = min(MAX_COLUMN_WIDTH, max(widths[index], _width(value) + 2))
    parts = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             u'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             u'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">']
    last = u"{}{}".format(column_letter(width - 1), max(len(rows), 1))
    parts.append(u'<dimension ref="A1:{}"/>'.format(last))
    view = u'<sheetView workbookViewId="0"{}'.format(u' tabSelected="1"' if selected else u"")
    if is_table and len(rows) > 1:
        parts.append(u'<sheetViews>{}><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                     u'state="frozen"/><selection pane="bottomLeft"/></sheetView>'
                     u'</sheetViews>'.format(view))
    else:
        parts.append(u'<sheetViews>{}/></sheetViews>'.format(view))
    parts.append(u'<sheetFormatPr defaultRowHeight="15"/><cols>')
    for index, size in enumerate(widths):
        parts.append(u'<col min="{0}" max="{0}" width="{1}" customWidth="1"/>'.format(
            index + 1, size))
    parts.append(u'</cols><sheetData>')
    for number, row in enumerate(rows):
        cells = [cell_xml(u"{}{}".format(column_letter(i), number + 1), value, number in bold)
                 for i, value in enumerate(row)]
        parts.append(u'<row r="{}">{}</row>'.format(number + 1, u"".join(cells)))
    parts.append(u'</sheetData>')
    if is_table and len(rows) > 1:
        parts.append(u'<autoFilter ref="A1:{}"/>'.format(last))
    parts.append(u'</worksheet>')
    return u"".join(parts)


def _quoted_sheet(name):
    return u"'{}'".format(name.replace(u"'", u"''"))


def workbook_parts(sheets):
    """{zip path: unicode XML} of the workbook."""
    count = len(sheets)
    parts = {}
    overrides = u"".join(
        u'<Override PartName="/xl/worksheets/sheet{}.xml" ContentType="application/'
        u'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.format(i + 1)
        for i in range(count))
    parts["[Content_Types].xml"] = (
        u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        u'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        u'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        u'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        u'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        u'officedocument.spreadsheetml.sheet.main+xml"/>'
        u'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
        u'officedocument.spreadsheetml.styles+xml"/>' + overrides + u'</Types>')
    parts["_rels/.rels"] = (
        u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        u'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        u'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    sheet_entries, names = [], []
    for index, (name, rows, _, is_table) in enumerate(sheets):
        sheet_entries.append(u'<sheet name="{}" sheetId="{}" r:id="rId{}"/>'.format(
            escape(name), index + 1, index + 1))
        if is_table and len(rows) > 1:
            width = max(len(r) for r in rows)
            names.append(u'<definedName name="_xlnm._FilterDatabase" localSheetId="{}" '
                         u'hidden="1">{}!$A$1:${}${}</definedName>'.format(
                             index, escape(_quoted_sheet(name)), column_letter(width - 1),
                             len(rows)))
    parts["xl/workbook.xml"] = (
        u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        u'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        u'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        u'<bookViews><workbookView activeTab="0"/></bookViews><sheets>' +
        u"".join(sheet_entries) + u'</sheets>' +
        (u'<definedNames>' + u"".join(names) + u'</definedNames>' if names else u"") +
        u'</workbook>')
    relations = [u'<Relationship Id="rId{0}" Type="http://schemas.openxmlformats.org/'
                 u'officeDocument/2006/relationships/worksheet" '
                 u'Target="worksheets/sheet{0}.xml"/>'.format(i + 1) for i in range(count)]
    relations.append(u'<Relationship Id="rId{}" Type="http://schemas.openxmlformats.org/'
                     u'officeDocument/2006/relationships/styles" '
                     u'Target="styles.xml"/>'.format(count + 1))
    parts["xl/_rels/workbook.xml.rels"] = (
        u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        u"".join(relations) + u'</Relationships>')
    parts["xl/styles.xml"] = (
        u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        u'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        u'<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
        u'<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>'
        u'<fills count="2"><fill><patternFill patternType="none"/></fill>'
        u'<fill><patternFill patternType="gray125"/></fill></fills>'
        u'<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        u'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        u'</cellStyleXfs><cellXfs count="2">'
        u'<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        u'<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        u'</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
        u'</cellStyles></styleSheet>')
    for index, (_, rows, bold, is_table) in enumerate(sheets):
        parts["xl/worksheets/sheet{}.xml".format(index + 1)] = sheet_xml(
            rows, bold, is_table, index == 0)
    return parts


PART_ORDER = ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
              "xl/_rels/workbook.xml.rels", "xl/styles.xml")


def audit_xlsx(report, today=None):
    """The audit report as .xlsx bytes."""
    parts = workbook_parts(workbook_sheets(report, today))
    names = list(PART_ORDER) + sorted((n for n in parts if n not in PART_ORDER),
                                      key=lambda n: int(n[len("xl/worksheets/sheet"):-4]))
    buffer = io.BytesIO()
    archive = zipfile.ZipFile(buffer, "w", COMPRESSION)
    try:
        for name in names:
            archive.writestr(str(name), parts[name].encode("utf-8"))
    finally:
        archive.close()
    return buffer.getvalue()


# ---- HTML ----

CSS = u"""
body{font-family:"Segoe UI",Arial,sans-serif;font-size:13px;color:#1f2328;background:#fff;
margin:24px auto;max-width:1100px;padding:0 16px;line-height:1.45}
h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:24px 0 8px}
h3{font-size:15px;margin:20px 0 6px}h4{font-size:13px;margin:14px 0 4px}
.meta{color:#57606a;margin:0 0 16px}
table{border-collapse:collapse;margin:6px 0 14px;width:100%}
th,td{border:1px solid #d0d7de;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f3f4f6;font-weight:600}tr:nth-child(even) td{background:#fafbfc}
code{background:#f3f4f6;padding:0 3px;border-radius:3px;font-family:Consolas,monospace}
p{margin:6px 0}
@media print{body{margin:0;max-width:none;font-size:11px}th{background:#eee}
tr{page-break-inside:avoid}h2,h3,h4{page-break-after:avoid}}
"""


def html_escape(value):
    return (clean_xml(value).replace(u"&", u"&amp;").replace(u"<", u"&lt;")
            .replace(u">", u"&gt;").replace(u"\"", u"&quot;"))


def _inline(value, marker, tag):
    """Pairs of marker -> <tag>...</tag>; an unpaired last marker stays as is."""
    pieces = value.split(marker)
    tail = u""
    if len(pieces) % 2 == 0:
        tail = marker + pieces.pop()
    out = []
    for index, piece in enumerate(pieces):
        out.append(u"<{0}>{1}</{0}>".format(tag, piece) if index % 2 else piece)
    return u"".join(out) + tail


def markdown_html(value):
    """The tiny markdown of the report blocks (##, ###, **bold**, `code`)."""
    lines = []
    for line in text(value).replace(u"\r", u"").split(u"\n"):
        level = 0
        while level < len(line) and line[level] == u"#":
            level += 1
        heading = level > 0 and line[level:level + 1] == u" "
        body = html_escape(line[level:].strip() if heading else line)
        body = _inline(_inline(body, u"**", u"strong"), u"`", u"code")
        if heading:
            lines.append(u"<h{0}>{1}</h{0}>".format(min(level, 4), body))
        elif body.strip():
            lines.append(u"<p>{}</p>".format(body))
    return u"\n".join(lines)


def table_html(block):
    if not block["rows"]:
        return u""
    parts = []
    if block.get("title"):
        parts.append(u"<h4>{}</h4>".format(html_escape(block["title"])))
    parts.append(u"<table><thead><tr>")
    parts.append(u"".join(u"<th>{}</th>".format(html_escape(c)) for c in block["columns"]))
    parts.append(u"</tr></thead><tbody>")
    for row in block["rows"]:
        parts.append(u"<tr>{}</tr>".format(u"".join(
            u"<td>{}</td>".format(html_escape(text(c))) for c in row)))
    parts.append(u"</tbody></table>")
    return u"".join(parts)


def audit_html(report, today=None):
    """The audit report (same blocks as the pyRevit output window) as a
    self-contained HTML page."""
    document = text(report.get("document"))
    body = []
    for block in reports.audit_report(report):
        if block is None:
            continue
        if block["kind"] == "table":
            body.append(table_html(block))
        else:
            body.append(markdown_html(block["text"]))
    return (u'<!DOCTYPE html>\n<html lang="pt-BR">\n<head>\n<meta charset="utf-8">\n'
            u'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            u'<title>Auditoria do template - {}</title>\n<style>{}</style>\n</head>\n<body>\n'
            u'<p class="meta">Gerado em {} pelo botão MCP &gt; Conferir &gt; Auditar template. '
            u'Só leitura: nada foi alterado no modelo.</p>\n{}\n</body>\n</html>\n').format(
                html_escape(document), CSS, html_escape(_today(today)),
                u"\n".join(b for b in body if b))


# ---- files ----

def export_file_name(document, today):
    """auditoria_<model>_<YYYY-MM-DD>.xlsx."""
    return os.path.splitext(reports.audit_file_name(document, today))[0] + u".xlsx"


def html_path_for(xlsx_path):
    return os.path.splitext(xlsx_path)[0] + u".html"


def xlsx_path_for(path):
    """The chosen path with the .xlsx extension (added when missing)."""
    path = text(path)
    return path if path.lower().endswith(u".xlsx") else path + u".xlsx"


def existing_outputs(path, exists=os.path.exists):
    """Files that saving to path would replace without the save dialog asking:
    the .html always, and the .xlsx when its extension had to be added."""
    xlsx_path = xlsx_path_for(path)
    found = []
    if xlsx_path != text(path) and exists(xlsx_path):
        found.append(xlsx_path)
    if exists(html_path_for(xlsx_path)):
        found.append(html_path_for(xlsx_path))
    return found


def is_locked_error(error):
    """True for "file in use" (the workbook open in Excel, for example)."""
    return getattr(error, "errno", None) == 13 or getattr(error, "winerror", None) in (32, 33)


class HtmlSaveError(Exception):
    """The .xlsx was saved but the .html was not."""

    def __init__(self, xlsx_path, error):
        Exception.__init__(self, u"{}".format(error))
        self.xlsx_path = xlsx_path
        self.error = error


def _write_replacing(path, write):
    """write(temp_path), then move the temp file onto path; the temp file is
    removed if anything fails (e.g. path open in Excel)."""
    temp = path + u".tmp"
    try:
        write(temp)
        if os.path.exists(path):
            os.remove(path)
        os.rename(temp, path)
    except Exception:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except Exception:
                pass
        raise


def write_audit_files(xlsx_path, report, today=None):
    """Write the .xlsx and, next to it, the .html; returns (xlsx_path, html_path).
    Raises HtmlSaveError when only the .xlsx could be written."""
    xlsx_path = xlsx_path_for(xlsx_path)
    html_path = html_path_for(xlsx_path)
    data = audit_xlsx(report, today)
    page = audit_html(report, today)

    def write_xlsx(path):
        with open(path, "wb") as stream:
            stream.write(data)

    def write_html(path):
        with io.open(path, "w", encoding="utf-8", newline=u"\n") as stream:
            stream.write(page)

    _write_replacing(xlsx_path, write_xlsx)
    try:
        _write_replacing(html_path, write_html)
    except Exception as error:
        raise HtmlSaveError(xlsx_path, error)
    return xlsx_path, html_path
