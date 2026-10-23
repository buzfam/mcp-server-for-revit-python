# -*- coding: utf-8 -*-
"""Reports of the ribbon buttons MCP > Conferir (revit_mcp/ribbon_reports.py):
pure module, no pyRevit stub needed."""
import json
import os
from pathlib import Path

from revit_mcp import ribbon_reports as rr

ROOT = Path(__file__).resolve().parents[2]


class FakeOutput:
    def __init__(self):
        self.calls = []

    def print_md(self, text):
        self.calls.append(("md", text))

    def print_table(self, table_data, columns, title=None):
        self.calls.append(("table", title, columns, table_data))


def link(element_id):
    return u"<a>{}</a>".format(element_id)


# ---- checklists file ----

def test_example_checklists_file_is_valid():
    raw = (ROOT / "config" / "checklists_pranchas.exemplo.json").read_text(encoding="utf-8")
    body, error = rr.load_checklists(raw)
    assert error is None
    assert "_comentario" not in body and body["limit"] == 1000
    assert [r["name"] for r in body["checklists"]][0] == u"Planta de layout"
    assert all(not k.startswith("_") for rule in body["checklists"] for k in rule)


def test_example_checklists_pass_the_route_validation(monkeypatch):
    import builtins
    import importlib
    import sys
    from types import SimpleNamespace
    for name, value in (("long", int), ("basestring", str), ("unicode", str)):
        monkeypatch.setattr(builtins, name, value, raising=False)
    monkeypatch.setitem(sys.modules, "pyrevit", SimpleNamespace(
        DB=SimpleNamespace(), routes=SimpleNamespace(), forms=SimpleNamespace(),
        script=SimpleNamespace()))
    modules = [m for m in sys.modules if m.startswith("revit_mcp.") and m != "revit_mcp.ribbon_reports"]
    for name in modules:
        monkeypatch.delitem(sys.modules, name)
    try:
        sheet_checks = importlib.import_module("revit_mcp.sheet_checks")
        raw = (ROOT / "config" / "checklists_pranchas.exemplo.json").read_text(encoding="utf-8")
        body, _ = rr.load_checklists(raw)
        options, error, errors = sheet_checks.parse_options(body)
        assert error is None, (error, errors)
        assert len(options["rules"]) == 6
    finally:
        for name in [m for m in sys.modules if m.startswith("revit_mcp.")
                     and m != "revit_mcp.ribbon_reports"]:
            sys.modules.pop(name, None)


def test_load_checklists_shapes_and_errors():
    body, error = rr.load_checklists(u'\ufeff[{"match": {"number_prefix": "A"}, "_nota": "x"}]')
    assert error is None and body["checklists"] == [{"match": {"number_prefix": "A"}}]
    body, _ = rr.load_checklists(json.dumps({"checklists": [{"match": {}}], "only_problems": True,
                                             "limit": 10}))
    assert body["only_problems"] is True and body["limit"] == 10
    assert u"JSON válido" in rr.load_checklists(u"{nope")[1]
    assert u"nenhum checklist" in rr.load_checklists(u'{"checklists": []}')[1]
    assert u"lista de checklists" in rr.load_checklists(u'"texto"')[1]


def test_checklist_errors_text():
    text = rr.checklist_errors_text("Invalid checklists", [{"index": 1, "error": "match must be"}])
    assert u"checklist 2: match must be" in text


# ---- sheets ----

SHEET_RESULT = {
    "status": "success",
    "summary": {"sheets_checked": 2, "sheets_total": 3, "truncated": True, "ok": 0, "missing": 1,
                "review": 1, "sheets_without_rule": 0,
                "problems_by_check": {"required_view": 1, "scale": 2},
                "placeholders_skipped": 1, "not_found": [u"Z-1"], "empty_check_skipped": 0},
    "sheets": [
        {"id": 12345, "number": u"A-101", "name": u"Planta | Layout", "rule": u"Planta de layout",
         "status": "review", "problems": [], "manual_checks": [u"cotas conferidas"]},
        {"id": 12346, "number": u"A-102", "name": u"Planta Superior", "rule": None,
         "status": "missing",
         "problems": [{"check": "required_view", "detail": u"faltando vista FloorPlan"},
                      {"check": "scale", "detail": u"vista 'Superior' em 1:75"}],
         "manual_checks": []},
        {"id": 12347, "number": u"A-103", "name": u"Ok", "rule": None, "status": "ok",
         "problems": [], "manual_checks": []}],
    "unplaced_views": {"count": 2, "by_type": {"FloorPlan": 1, "Section": 1},
                       "views": [{"id": 2222, "name": u"Estudo", "type": "FloorPlan"}],
                       "truncated": False},
}


def test_sheet_report_summary_tables_and_checklist():
    blocks = rr.sheet_report(SHEET_RESULT, source=u"C:\\cfg.json", link=link)
    text = rr.as_text(blocks)
    assert u"**2 de 3 folhas conferidas** · OK: 0 · Faltando: 1 · Revisar: 1" in text
    assert u"cortada" in text and u"Z-1" in text and u"placeholder" in text
    assert u"Escala | 2" in text  # most frequent check first
    overview = [b for b in blocks if b and b["kind"] == "table" and b["title"] == u"Folhas"][0]
    assert overview["rows"][0][:5] == [u"<a>12345</a>", u"A-101", u"Planta / Layout",
                                       u"Planta de layout", u"Revisar"]
    assert overview["rows"][1][3] == u"-" and overview["rows"][1][4] == u"Faltando"
    assert u"### <a>12346</a> A-102 - Planta Superior (Faltando)" in text
    assert u"Vista obrigatória | faltando vista FloorPlan" in text
    assert u"- \u2610 cotas conferidas" in text
    assert u"### <a>12347</a>" not in text  # sheet without problems has no detail block
    assert u"Vistas fora de pranchas: 2" in text and u"Planta: 1" in text
    assert u"<a>2222</a> | Estudo | Planta" in text


def test_sheet_report_without_link_or_sheets():
    text = rr.as_text(rr.sheet_report({"summary": {}, "sheets": []}))
    assert u"Nenhuma folha" in text


def test_render_prints_md_and_tables_and_skips_empty():
    output = FakeOutput()
    rr.render(output, [rr.md(u"# t"), None, rr.table(u"", [u"A"], []),
                       rr.table(u"T", [u"A", u"B"], [[1, u"x"]])])
    assert output.calls == [("md", u"# t"), ("table", u"T", [u"A", u"B"], [[u"1", u"x"]])]


# ---- materials ----

MATERIAL_RESULT = {
    "document": u"Apartamento Térreo", "categories": [u"Mobiliário"], "elements_checked": 3,
    "types_checked": 2, "materials_checked": 2, "problems_total": 4, "returned": 4,
    "truncated": False,
    "summary": {"name_pattern": 1, "duplicate_names": 1, "generic_shared": 0,
                "single_material": 1, "category_material_only": 1, "no_material": 0,
                "painted_types": 1, "mesh_only_types": 1},
    "problems": [
        {"rule": "category_material_only", "severity": "error", "family": u"Parede", "type": u"A",
         "category": u"Paredes", "element_id": 30, "material": u"Padrão"},
        {"rule": "name_pattern", "severity": "error", "material": u"madeira"},
        {"rule": "duplicate_names", "severity": "review", "materials": [u"M 01", u"M_02"]},
        {"rule": "single_material", "severity": "review", "family": u"Sofá", "type": u"3 lugares",
         "category": u"Mobiliário", "element_id": 20, "painted": [u"TINTA"], "note": "meshes"},
    ],
}


def test_material_report_grouped_by_rule():
    text = rr.as_text(rr.material_report(MATERIAL_RESULT, link=link))
    assert u"Modelo: **Apartamento Térreo** · Categorias: Mobiliário" in text
    assert u"**4 problema(s)**" in text
    assert u"Só o material da categoria | 1" in text
    order = [text.index(u"### " + label) for label in (
        u"Só o material da categoria", u"Nome fora do padrão", u"Nomes repetidos",
        u"Mobiliário com um material só")]
    assert order == sorted(order)
    assert u"<a>30</a> | Parede | A | Paredes | Padrão" in text
    assert u"Revisar | M 01, M_02" in text
    assert u"pintado: TINTA; geometria só de malha" in text
    assert u"### Material genérico" not in text


def test_material_report_lists_shared_families_and_more_line():
    problems = [{"rule": "generic_shared", "severity": "error", "material": u"GEN",
                 "families": [u"A", u"B", u"C"]}] + \
        [{"rule": "name_pattern", "severity": "error", "material": u"m{}".format(i)}
         for i in range(rr.MAX_LISTED + 2)]
    text = rr.as_text(rr.material_report({"problems": problems, "summary": {}}))
    assert u"GEN | A, B, C" in text and u"... e mais 2." in text


def test_material_report_without_problems():
    assert u"Nenhum problema" in rr.as_text(rr.material_report({"summary": {}, "problems": []}))


def test_material_report_view_without_elements_is_not_green():
    text = rr.as_text(rr.material_report({"summary": {}, "problems": [], "elements_checked": 0,
                                          "view": {"id": 5, "name": u"Folha"}}))
    assert u"Nenhum elemento das categorias verificadas visível nesta vista" in text
    assert u"Nenhum problema" not in text


def test_material_view_error():
    for view_type in ("DrawingSheet", "Schedule", "Legend", "DraftingView", "PanelSchedule"):
        assert u"abra uma vista 3D, planta ou elevação" in rr.material_view_error(view_type, False)
    assert u"Folha" in rr.material_view_error("DrawingSheet", False)
    assert rr.material_view_error("ThreeD", True).startswith(u"A vista ativa é um modelo")
    for view_type in ("ThreeD", "FloorPlan", "Elevation", "Section"):
        assert rr.material_view_error(view_type, False) is None


# ---- PDF ----

PLAN = {"status": "dry_run", "folder": u"C:\\Emissoes\\P-07", "folder_exists": False,
        "issued_at": u"2026-10-21T09:00:00",
        "files": [{"file": u"P-07_A-101_R02.pdf", "sheets": [u"A-101"], "exists": True},
                  {"file": u"P-07_A-102.pdf", "sheets": [u"A-102"], "exists": False}],
        "skipped": [{"sheet_number": u"A-900", "reason": "placeholder"}],
        "renamed_to_avoid_duplicates": [u"P-07_A-102.pdf"],
        "warnings": [u"Could not set PDFExportOptions.HideScopeBoxes"],
        "warning": u"1 file(s) already exist; the real run needs overwrite=true"}


def test_pdf_plan_text():
    message, details = rr.pdf_plan_text(PLAN)
    assert message == u"Emitir 2 PDFs de 2 folhas?"
    assert u"C:\\Emissoes\\P-07 (será criada)" in details
    assert u"- P-07_A-101_R02.pdf  [JÁ EXISTE]" in details
    assert u"A-900: folha reservada (placeholder)" in details
    assert u"sufixo" in details and u"HideScopeBoxes" in details
    assert u"1 arquivo(s) já existem" in details and u"overwrite" not in details
    assert rr.existing_files(PLAN) == [u"P-07_A-101_R02.pdf"]
    single = rr.pdf_plan_text({"folder": u"x", "folder_exists": True,
                               "files": [{"file": u"a.pdf", "sheets": [u"A", u"B"]}]})
    assert single[0] == u"Emitir 1 PDF de 2 folhas?" and u"criada" not in single[1]


def test_pdf_plan_text_caps_long_lists():
    files = [{"file": u"f{}.pdf".format(i), "sheets": [u"{}".format(i)]} for i in range(20)]
    assert u"... e mais 5" in rr.pdf_plan_text({"folder": u"x", "files": files})[1]


def test_pdf_replace_and_result_texts():
    message, details = rr.pdf_replace_text([u"a.pdf"])
    assert message == u"Substituir 1 arquivo que já existe(m) na pasta?" and u".bak" in details
    message, details, ok = rr.pdf_result_text({
        "folder": u"C:\\x", "exported": 2, "replaced": [u"a.pdf"], "errors": [],
        "manifest": u"C:\\x\\emissao.json", "warnings": [u"w"]})
    assert ok and message == u"2 PDF(s) emitido(s)."
    assert u"Substituídos: a.pdf" in details and u"emissao.json" in details
    message, details, ok = rr.pdf_result_text({
        "folder": u"C:\\x", "exported": 1, "errors": [{"file": u"b.pdf", "error": u"falhou"}]})
    assert not ok and u"1 com erro" in message and u"Erro em b.pdf: falhou" in details


def test_pdf_error_text_reads_the_data_not_the_status():
    assert u"bloqueado" in rr.pdf_error_text({"error": u"file in use or locked: a.pdf (x)"})
    assert u"Apareceram" in rr.pdf_error_text({
        "error": u"1 file(s) appeared in the folder while the approval dialog was open; nothing "
                 u"was exported", "existing": [u"a.pdf"]})
    assert u"confirme a substituição" in rr.pdf_error_text({
        "status": "error", "error": u"1 file(s) already exist", "existing": [u"a.pdf"]})
    assert u"conflito" in rr.pdf_error_text({"error": u"x", "conflicts": [u"a.pdf"]})
    assert u"2022" in rr.pdf_error_text({"error": u"Native PDF export needs Revit 2022 or newer"})
    assert rr.pdf_error_text(None) == u"A emissão não foi feita: resposta inesperada"
    # The status still helps when the data says nothing more.
    assert u"bloqueado" in rr.pdf_error_text({"error": u"x"}, 423)
    assert rr.pdf_error_text({}, 500) == u"A emissão não foi feita: erro 500"


def test_plan_and_run_are_recognized_from_the_data():
    assert rr.is_plan(PLAN) and not rr.is_run_result(PLAN)
    run = {"status": "success", "files": [], "exported": 0, "folder": u"x"}
    assert rr.is_run_result(run) and not rr.is_plan(run)
    assert rr.is_run_result(dict(run, status="error", errors=[{"file": u"a"}]))
    blocked = dict(PLAN, status="error", existing=[u"a.pdf"], error=u"already exist")
    assert not rr.is_plan(blocked) and not rr.is_run_result(blocked)
    assert not rr.is_plan({"error": u"x"}) and not rr.is_run_result(None)


def test_replacement_guard():
    folder = u"C:\\Emissoes\\P-07"
    approved = rr.replace_paths(PLAN)
    assert approved == [os.path.join(folder, u"P-07_A-101_R02.pdf")]
    approve = rr.replacement_guard(approved)
    assert approve(u"Export", {"_will_replace": approved}) is None          # same files
    assert approve(u"Export", {"_files": [u"a.pdf"]}) is None               # nothing replaced
    stop = approve(u"Export", {"_will_replace": approved + [os.path.join(folder, u"novo.pdf")]})
    assert stop.status == 409 and u"1 arquivo(s) apareceram" in stop.data["error"]
    assert rr.pdf_error_text(stop.data, stop.status).startswith(u"Há conflito")
    assert rr.replacement_guard([])(u"Export", {"_will_replace": [u"x.pdf"]}) is not None


def test_overwrite_decision_from_the_plan():
    assert rr.needs_overwrite(PLAN) is True
    plan = dict(PLAN, files=[{"file": u"a.pdf", "sheets": [u"A"], "exists": False}])
    assert rr.needs_overwrite(plan) is False and rr.replace_paths(plan) == []


def test_pdf_history_summary():
    assert rr.pdf_history_summary({"exported": 2, "folder": u"C:\\x", "errors": []}) == \
        u"2 PDF(s) em C:\\x"
    assert rr.pdf_history_summary({"exported": 1, "folder": u"y", "errors": [{}]}) == \
        u"1 PDF(s) em y, 1 com erro"


# ---- checklists location ----

def test_checklists_paths():
    user, example = rr.checklists_paths(u"/appdata", u"/ext")
    assert user == os.path.join(u"/appdata", u"RevitMCPPython", u"checklists_pranchas.json")
    assert example == os.path.join(u"/ext", u"config", u"checklists_pranchas.exemplo.json")
    assert rr.checklists_paths(None, u"/ext")[0] is None


def test_prepare_checklists_copies_the_example_once(tmp_path):
    appdata = tmp_path / "appdata"
    path, created, error = rr.prepare_checklists(str(appdata), str(ROOT))
    assert error is None and created is True
    assert path == str(appdata / "RevitMCPPython" / "checklists_pranchas.json")
    Path(path).write_text(u'{"checklists": [{"match": {"number_prefix": "A"}}]}', encoding="utf-8")
    again, created, error = rr.prepare_checklists(str(appdata), str(ROOT))
    assert again == path and created is False and error is None
    assert "number_prefix" in Path(path).read_text(encoding="utf-8")     # office file kept


def test_prepare_checklists_errors(tmp_path):
    assert rr.prepare_checklists(None, str(ROOT))[2] == u"pasta APPDATA não encontrada"
    assert u"exemplo" in rr.prepare_checklists(str(tmp_path), str(tmp_path / "nada"))[2]

    def fail(source, target):
        raise IOError("disco cheio")
    path, created, error = rr.prepare_checklists(str(tmp_path), str(ROOT), copy=fail)
    assert path is None and u"disco cheio" in error


def test_example_file_name_is_not_the_office_file():
    assert not (ROOT / "config" / "checklists_pranchas.json").exists()


# ---- audit ----

AUDIT = {
    "status": "success", "document": u"Template CV", "is_template_file": True,
    "revit_version": "2024", "limit": 100,
    "sections": {
        "view_templates": {"count": 3, "unused_count": 1, "truncated": False,
                           "items": [{"id": 7, "name": u"Planta 1:50", "unused": True},
                                     {"id": 8, "name": u"Corte", "unused": False}]},
        "levels_grids": {"levels": {"count": 2, "unused_count": None, "items": [], "truncated": False},
                         "grids": {"count": 0, "unused_count": None, "items": [], "truncated": False}},
        "sheets": {"sheets": {"count": 1, "unused_count": 1, "truncated": False,
                              "items": [{"id": 9, "number": u"A-1", "name": u"Capa", "unused": True}]},
                   "title_blocks": {"count": 1, "unused_count": 0, "items": [], "truncated": False}},
        "naming": {"views": {"pattern": u"^[A-Z]{2}.+", "checked": 4, "violations_count": 1,
                             "violations": [{"id": 11, "name": u"vista"}], "truncated": False}},
        "purge_preview": {"count": 2, "by_kind": {"FamilySymbol": 2}, "source": "revit_purge",
                          "items": [{"id": 21, "kind": "FamilySymbol", "category": u"Portas",
                                     "name": u"Porta: 80"}], "truncated": True,
                          "not_in_revit_purge": {
                              "view_templates": {"count": 1, "items": []},
                              "filters": {"count": 0, "items": []}}},
    },
    "checklist": [{"item": 7, "title": u"Categorias e visibilidades", "coverage": u"conferir a olho",
                   "sections": [], "check_manually": u"Visibilidade/Gráficos"},
                  {"item": 2, "title": u"Níveis e eixos", "coverage": "api",
                   "sections": ["levels_grids"]}],
    "section_errors": {"families": u"skipped: time budget"},
}


def test_audit_report():
    text = rr.as_text(rr.audit_report(AUDIT, link=link))
    assert u"Modelo: **Template CV** (arquivo de template .rte) · Revit 2024" in text
    assert u"Modelos de vista | 3 | 1" in text
    assert u"Níveis e eixos / níveis | 2 | -" in text
    assert u"Folhas / folhas | 1 | 1" in text and u"Folhas / carimbos | 1 | 0" in text
    assert u"### Sem uso: Modelos de vista (1)" in text
    assert u"<a>7</a> | Planta 1:50" in text and u"<a>8</a>" not in text
    assert u"<a>9</a> | A-1 - Capa" in text
    assert u"Nomenclatura de views: 1 fora do padrão" in text and u"<a>11</a> | vista" in text
    assert u"Prévia do Limpar não utilizados: 2 item(ns)" in text and u"Nada foi apagado" in text
    assert u"<a>21</a> | FamilySymbol | Portas | Porta: 80" in text
    assert u"... e mais 1 (veja a planilha Excel para a lista completa)." in text
    assert u"JSON" not in text
    assert u"1 modelos de vista sem uso" in text
    assert u"Níveis e eixos | automática | Níveis e eixos" in text
    assert u"Famílias: skipped: time budget" in text


def test_audit_file_name():
    assert rr.audit_file_name(u"Casa Árvore: v2", u"2026-10-21") == \
        u"auditoria_Casa_Árvore__v2_2026-10-21.json"
    assert rr.audit_file_name(u"", u"d") == u"auditoria_modelo_d.json"
