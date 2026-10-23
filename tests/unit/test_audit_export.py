# -*- coding: utf-8 -*-
"""Excel and HTML files of the Auditar template button (revit_mcp/audit_export.py):
pure module, no pyRevit stub needed."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

import pytest

from revit_mcp import audit_export as ae

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
TRAPEZOID = u"Montante de canto trapezóide"


def summary(items, unused_count=None, truncated=False, count=None, rule=None):
    result = {"count": len(items) if count is None else count, "unused_count": unused_count,
              "items": items, "truncated": truncated}
    if rule:
        result["unused_rule"] = rule
    return result


REPORT = {
    "status": "success", "document": u"Template CV <Arq> & Cia", "is_template_file": True,
    "revit_version": "2024", "limit": 1000, "unused_source": "revit_purge",
    "sections": {
        "browser_organization": summary([
            {"id": 101, "name": u"Por disciplina", "type": u"Views", "current": True,
             "folders": [u"Disciplina", u"Família e tipo"]}]),
        "levels_grids": {
            "levels": summary([{"id": 201, "name": u"Térreo", "elevation_m": 0.0, "plan_views": 2},
                               {"id": 202, "name": u"Cobertura", "elevation_m": 3.048,
                                "plan_views": 0}]),
            "grids": summary([])},
        "phases": {
            "phases": summary([{"order": 1, "id": 301, "name": u"Existente", "views": 1}]),
            "phase_filters": summary(
                [{"id": 302, "name": u"Mostrar novo", "views_and_templates": 0, "unused": True,
                  "presentation": {"new": "ShowByCategory", "demolished": "DontShow"}}],
                unused_count=1, rule="no view or view template uses it")},
        "view_templates": summary(
            [{"id": 7, "name": u"Planta 1:50", "view_type": "FloorPlan", "views": 0,
              "filters": 2, "unused": True},
             {"id": 8, "name": u"Corte", "view_type": "Section", "views": 3, "filters": 0,
              "unused": False}], unused_count=1, rule="no view uses this template"),
        "filters": summary(
            [{"id": 401, "name": u"Paredes a demolir", "kind": "parameter", "views": 0,
              "templates": 0, "unused": True, "categories": 2}], unused_count=1),
        "views": summary(
            [{"id": 501, "name": u"Planta Térreo", "view_type": "FloorPlan", "scale": u"1:50",
              "template": u"Planta 1:50", "on_sheet": False, "unused": True}], unused_count=1),
        "line_styles": {
            "styles": summary([{"id": 601, "name": u"CV - Linha fina", "weight": 1,
                                "color": "#FF0000", "pattern": u"<solid>", "lines": 0,
                                "builtin": False, "unused": True}], unused_count=1),
            "line_patterns": summary([{"id": 602, "name": u"Tracejado", "unused": False}],
                                     unused_count=0),
            "fill_patterns": summary([{"id": 603, "name": u"Hachura 45°", "target": "Drafting",
                                       "unused": True}], unused_count=1)},
        "annotation_types": {
            "dimension_types": summary([{"id": 701, "name": u"Linear 2mm", "instances": 4,
                                         "unused": False, "style": "Linear"}], unused_count=0),
            "text_types": summary([], unused_count=0),
            "tag_and_symbol_types": summary([], unused_count=0)},
        "families": dict(summary(
            [{"id": 801, "name": TRAPEZOID, "category": u"Montantes de parede cortina",
              "in_place": False, "types": 1, "unused_types": 1, "instances": 0, "unused": True},
             {"id": 802, "name": u"Porta 1 folha", "category": u"Portas", "in_place": False,
              "types": 3, "unused_types": 0, "instances": 12, "unused": False}],
            unused_count=1, count=40, truncated=True),
            by_category={u"Portas": {"families": 1, "unused": 0}}),
        "shared_parameters": dict(summary(
            [{"id": 901, "name": u"CV_Código", "guid": "abc-123", "bound": True,
              "binding": "instance", "categories": [u"Portas", u"Janelas"],
              "categories_count": 2}]), bound_count=1),
        "project_parameters": summary([{"id": 902, "shared": False, "name": u"Acabamento",
                                        "binding": "type", "categories": [u"Paredes"],
                                        "categories_count": 1}]),
        "materials": summary([{"id": 1001, "name": u"Concreto \x01moldado", "unused": True,
                               "class": u"Concreto", "category": None}], unused_count=1),
        "schedules": summary([{"id": 1101, "name": u"Tabela de portas", "category": u"Portas",
                               "key_schedule": False, "on_sheet": True, "unused": False}],
                             unused_count=0),
        "sheets": {
            "sheets": summary([{"id": 9, "number": u"A-1", "name": u"Capa", "placeholder": False,
                                "views": 0, "title_block": None, "unused": True}],
                              unused_count=1),
            "title_blocks": summary([{"id": 1201, "name": u"A1: CV", "sheets": 1,
                                      "unused": False}], unused_count=0)},
        "export_settings": {
            "dwg": dict(summary([{"id": 1301, "name": u"DWG escritório"}]), active=None),
            "pdf": summary([]), "print_settings": summary([]), "view_sheet_sets": summary([])},
        "naming": {"views": {"pattern": u"^[A-Z]{2} - .+$", "checked": 4,
                             "violations_count": 1,
                             "violations": [{"id": 501, "name": u"Planta Térreo"}],
                             "truncated": False}},
        "purge_preview": {
            "count": 2, "by_kind": {"FamilySymbol": 2}, "source": "revit_purge",
            "items": [{"id": 1401, "kind": "FamilySymbol", "category": u"Portas",
                       "name": u"Porta: 80"},
                      {"id": 1402, "kind": "FamilySymbol", "category": u"Montantes",
                       "name": TRAPEZOID}], "truncated": False,
            "not_in_revit_purge": {
                "view_templates": summary([{"id": 7, "name": u"Planta 1:50"}]),
                "filters": summary([])},
            "note": "Preview only"},
    },
    "checklist": [
        {"item": n, "title": u"Item {}".format(n), "coverage": "api" if n % 2 else "parcial",
         "sections": ["views"]} for n in range(1, 20)],
    "section_errors": {"export_settings": u"skipped: time budget"},
    "time_budget": {"seconds": 240.0, "skipped_sections": ["export_settings"],
                    "hint": "call again with only the skipped sections"},
}
REPORT["checklist"][6] = {"item": 7, "title": u"Categorias e visibilidades",
                          "coverage": u"conferir a olho", "sections": [],
                          "check_manually": u"Visibilidade/Gráficos"}


@pytest.fixture(scope="module")
def workbook():
    data = ae.audit_xlsx(REPORT, today="2026-10-22")
    archive = zipfile.ZipFile(io.BytesIO(data))
    parts = dict((name, archive.read(name)) for name in archive.namelist())
    return data, parts


def sheet_names(parts):
    root = ET.fromstring(parts["xl/workbook.xml"])
    return [s.get("name") for s in root.find("m:sheets", NS)]


def sheet_rows(parts, name):
    index = sheet_names(parts).index(name) + 1
    root = ET.fromstring(parts["xl/worksheets/sheet{}.xml".format(index)])
    rows = []
    for row in root.find("m:sheetData", NS):
        values = []
        for c in row:
            if c.get("t") == "inlineStr":
                values.append(c.find("m:is/m:t", NS).text or u"")
            else:
                value = c.find("m:v", NS).text
                values.append(float(value) if "." in value else int(value))
        rows.append(values)
    return rows


def test_package_parts(workbook):
    _, parts = workbook
    types = ET.fromstring(parts["[Content_Types].xml"])
    overrides = [o.get("PartName") for o in types]
    assert "/xl/workbook.xml" in overrides and "/xl/styles.xml" in overrides
    sheets = [n for n in parts if n.startswith("xl/worksheets/")]
    assert len(sheets) == len(sheet_names(parts))
    for name in sheets:
        assert "/" + name in overrides
    for name, data in parts.items():
        ET.fromstring(data)  # every part is well-formed XML
    relations = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    assert len(relations) == len(sheets) + 1


def test_sheet_names_are_valid_and_unique(workbook):
    _, parts = workbook
    names = sheet_names(parts)
    assert names[:2] == [u"Resumo", u"Checklist"]
    assert u"Famílias" in names and u"Materiais" in names and u"Níveis" in names
    assert u"Limpeza (prévia)" in names and u"Nomenclatura" in names
    assert u"Padrões de preenchimento" in names and u"Carimbos" in names
    assert u"Eixos" not in names  # empty lists get no sheet
    assert len(set(n.lower() for n in names)) == len(names)
    for name in names:
        assert 0 < len(name) <= 31 and not re.search(r"[\[\]:*?/\\]", name)


def test_sheet_name_helper():
    used = set()
    assert ae.sheet_name(u"a/b:c[d]*?", used) == u"a b c d"
    long = u"Configurações de exportação muito longas"
    first, second = ae.sheet_name(long, used), ae.sheet_name(long, used)
    assert len(first) == 31 and len(second) <= 31 and second.endswith(u" (2)")
    assert ae.sheet_name(u"''", used) == u"Planilha"
    assert ae.sheet_name(u"History", used) == u"History (2)"
    assert ae.sheet_name(u"history", used) == u"history (2) (2)"
    cut = ae.sheet_name(u"x" * 30 + u"'abc", used)
    assert cut == u"x" * 30 and not cut.endswith(u"'")
    assert ae.sheet_name(u"x" * 30 + u"'abc", used) == u"x" * 27 + u" (2)"


def test_summary_sheet(workbook):
    _, parts = workbook
    rows = sheet_rows(parts, u"Resumo")
    flat = [r for r in rows if r]
    assert [u"Modelo", u"Template CV <Arq> & Cia"] in flat
    assert [u"Data", u"2026-10-22"] in flat
    assert [u"Tipo de arquivo", u".rte (template)"] in flat
    families = [r for r in flat if r[0] == u"Famílias"][0]
    assert families[1:4] == [40, 1, 2] and u"lista cortada: 2 de 40" in families[4]
    assert [r for r in flat if r[0] == u"Limpeza (prévia)"][0][1] == 2
    assert any(r[0] == u"Configurações de exportação" for r in flat)  # section_errors
    assert any(r[0] == u"Tempo esgotado" for r in flat)


def test_section_sheet_columns_and_values(workbook):
    _, parts = workbook
    rows = sheet_rows(parts, u"Famílias")
    header = rows[0]
    assert header[0] == u"Nome" and header[-2:] == [u"Situação", u"Id"]
    assert u"Categoria" in header and u"Instâncias" in header
    first = dict(zip(header, rows[1]))
    assert first[u"Nome"] == TRAPEZOID  # accents round-trip
    assert first[u"Situação"] == u"Sem uso" and first[u"Id"] == 801
    assert first[u"Instâncias"] == 0 and first[u"Família no local"] == u"Não"
    assert dict(zip(header, rows[2]))[u"Situação"] == u"Em uso"
    shared = sheet_rows(parts, u"Parâmetros compartilhados")
    row = dict(zip(shared[0], shared[1]))
    assert row[u"Categorias"] == u"Portas; Janelas" and row[u"Vínculo"] == u"Instância"
    assert u"Situação" not in shared[0]  # section does not track unused
    levels = dict(zip(*sheet_rows(parts, u"Níveis")[:3:2]))
    assert levels[u"Elevação (m)"] == 3.048
    views = dict(zip(*sheet_rows(parts, u"Vistas")[:2]))
    assert views[u"Tipo de vista"] == u"Planta"


def test_columns_do_not_repeat_and_sub_lists_follow_report_order(workbook):
    _, parts = workbook
    names = sheet_names(parts)
    assert names.index(u"Níveis") < names.index(u"Eixos" if u"Eixos" in names else u"Fases")
    assert names.index(u"Fases") < names.index(u"Filtros de fase")
    assert names.index(u"Estilos de linha") < names.index(u"Padrões de linha") < \
        names.index(u"Padrões de preenchimento")
    assert names.index(u"Folhas") < names.index(u"Carimbos")
    header = sheet_rows(parts, u"Filtros")[0]
    assert u"Tipo de filtro" in header and len(set(header)) == len(header)
    assert sheet_rows(parts, u"Organização do navegador")[1][1] == u"Vistas"
    columns, _ = ae.item_table({"count": 1, "unused_count": None,
                                "items": [{"id": 1, "kind": "x", "zzz": 1, "xyz": 2}]})
    assert len(set(columns)) == len(columns)


def test_nan_and_infinity_are_written_as_text():
    report = {"document": u"x", "sections": {"levels_grids": {"levels": {
        "count": 2, "unused_count": None, "truncated": False,
        "items": [{"id": 1, "name": u"A", "elevation_m": float("nan")},
                  {"id": 2, "name": u"B", "elevation_m": float("inf")}]}}}}
    parts = dict((n, zipfile.ZipFile(io.BytesIO(ae.audit_xlsx(report))).read(n))
                 for n in zipfile.ZipFile(io.BytesIO(ae.audit_xlsx(report))).namelist())
    rows = sheet_rows(parts, u"Níveis")
    assert rows[1][1] == u"nan" and rows[2][1] == u"inf"


def test_control_chars_are_stripped(workbook):
    _, parts = workbook
    rows = sheet_rows(parts, u"Materiais")
    assert rows[1][0] == u"Concreto moldado"


def test_checklist_and_purge_sheets(workbook):
    _, parts = workbook
    rows = sheet_rows(parts, u"Checklist")
    assert rows[0][:3] == [u"Item", u"Assunto", u"Status"] and len(rows) == 20
    seventh = rows[7]
    assert seventh[0] == 7 and seventh[2] == u"conferir a olho" and \
        seventh[-1] == u"Visibilidade/Gráficos"
    purge = sheet_rows(parts, u"Limpeza (prévia)")
    assert purge[0] == [u"Tipo de elemento", u"Categoria", u"Nome", u"Classe (API)", u"Id"]
    assert purge[1][0] == u"Tipo de família" and purge[1][3] == u"FamilySymbol"
    assert purge[2][2:] == [TRAPEZOID, u"FamilySymbol", 1402]
    assert purge[3][0].startswith(u"Modelo de vista") and purge[3][-1] == 7
    assert ae.element_kind("UnknownApiClass") == u"UnknownApiClass"


def test_header_bold_frozen_and_filtered(workbook):
    _, parts = workbook
    index = sheet_names(parts).index(u"Famílias") + 1
    root = ET.fromstring(parts["xl/worksheets/sheet{}.xml".format(index)])
    assert root.find("m:sheetViews/m:sheetView/m:pane", NS).get("state") == "frozen"
    assert root.find("m:autoFilter", NS).get("ref").startswith("A1:")
    first_cell = root.find("m:sheetData/m:row/m:c", NS)
    assert first_cell.get("s") == "1"
    names = ET.fromstring(parts["xl/workbook.xml"]).find("m:definedNames", NS)
    assert any(n.get("name") == "_xlnm._FilterDatabase" for n in names)


def test_xml_escape_and_clean():
    assert ae.escape(u"a<b>&\"c'") == u"a&lt;b&gt;&amp;&quot;c&apos;"
    assert ae.clean_xml(u"a\x00b\x0bc\td￾") == u"ab" + u"c\td"
    assert ae.clean_xml(u"x\ud800y") == u"xy"
    assert ae.column_letter(0) == u"A" and ae.column_letter(25) == u"Z" and \
        ae.column_letter(26) == u"AA"


def test_openpyxl_loads_workbook(workbook):
    openpyxl = pytest.importorskip("openpyxl")
    data, _ = workbook
    book = openpyxl.load_workbook(io.BytesIO(data))
    assert book.sheetnames[0] == u"Resumo"
    sheet = book[u"Famílias"]
    assert sheet["A2"].value == TRAPEZOID


def test_minimal_report():
    data = ae.audit_xlsx({"document": u"x", "sections": {}})
    parts = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "xl/worksheets/sheet1.xml" in parts and "xl/worksheets/sheet2.xml" not in parts
    assert u"<h2>Auditoria do template</h2>" in ae.audit_html({"document": u"x", "sections": {}})


def test_html_page():
    page = ae.audit_html(REPORT, today="2026-10-22")
    assert page.startswith(u"<!DOCTYPE html>") and u'<meta charset="utf-8">' in page
    assert u"<h2>Auditoria do template</h2>" in page
    assert u"<h3>Sem uso: Famílias (1)</h3>" in page
    assert u"Checklist do template (19 itens)" in page
    assert u"Template CV &lt;Arq&gt; &amp; Cia" in page and u"<Arq>" not in page
    assert u"<strong>Template CV &lt;Arq&gt; &amp; Cia</strong>" in page
    assert u"<code>^[A-Z]{2} - .+$</code>" in page
    assert TRAPEZOID in page and u"2026-10-22" in page
    assert u"veja o JSON" not in page and u"veja a planilha Excel" not in page  # nothing hidden
    assert u"http" not in page.replace(u"http-equiv", u"")  # no external resources


def test_markdown_inline():
    assert ae.markdown_html(u"a **b** c `d` e **f") == u"<p>a <strong>b</strong> c <code>d</code> e **f</p>"
    assert ae.markdown_html(u"### T\nlinha") == u"<h3>T</h3>\n<p>linha</p>"


def test_existing_outputs_and_locked_error(tmp_path):
    base = tmp_path / u"aud"
    assert ae.existing_outputs(str(base)) == []
    (tmp_path / u"aud.xlsx").write_bytes(b"x")
    (tmp_path / u"aud.html").write_text(u"x")
    assert ae.existing_outputs(str(base)) == [str(base) + u".xlsx", str(base) + u".html"]
    # with the extension the save dialog already asked about the .xlsx
    assert ae.existing_outputs(str(base) + u".xlsx") == [str(base) + u".html"]
    assert ae.xlsx_path_for(u"a.XLSX") == u"a.XLSX" and ae.xlsx_path_for(u"a") == u"a.xlsx"
    assert ae.is_locked_error(PermissionError(13, "denied"))
    assert ae.is_locked_error(OSError(2, "missing")) is False


def test_writer_replaces_and_reports_html_failure(tmp_path, monkeypatch):
    target = tmp_path / u"aud.xlsx"
    target.write_bytes(b"old")
    xlsx, html = ae.write_audit_files(str(target), REPORT)
    assert zipfile.is_zipfile(xlsx) and not list(tmp_path.glob("*.tmp"))
    real_open = ae.io.open

    def failing_open(path, *args, **kwargs):
        if path.endswith(u".html.tmp"):
            raise PermissionError(13, "in use")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(ae.io, "open", failing_open)
    with pytest.raises(ae.HtmlSaveError) as caught:
        ae.write_audit_files(str(target), REPORT)
    assert caught.value.xlsx_path == str(target) and ae.is_locked_error(caught.value.error)
    assert not list(tmp_path.glob("*.tmp"))


def test_file_names_and_writer(tmp_path):
    assert ae.export_file_name(u"Casa Árvore", u"2026-10-22") == \
        u"auditoria_Casa_Árvore_2026-10-22.xlsx"
    xlsx, html = ae.write_audit_files(str(tmp_path / u"saída"), REPORT, today="2026-10-22")
    assert xlsx.endswith(u"saída.xlsx") and html.endswith(u"saída.html")
    assert zipfile.is_zipfile(xlsx)
    with io.open(html, encoding="utf-8") as stream:
        assert TRAPEZOID in stream.read()
