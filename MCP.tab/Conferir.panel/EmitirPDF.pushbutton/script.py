# -*- coding: utf-8 -*-
"""Emitir PDF (sem IA): a mesma emissão da tool export_sheets_pdf.

1. Escolher as folhas (todas ou algumas).
2. Plano (dry run): pasta, nomes dos arquivos e avisos; confirmar.
3. Se algum arquivo já existe, confirmar a substituição (o antigo vira .bak
   até o novo ser gravado e volta se a exportação falhar).
4. Exportar, mostrar o resultado e oferecer abrir a pasta.
Quem clica é quem aprova, por isso não há o diálogo de aprovação do MCP; a
emissão fica registrada no Histórico. As decisões são tomadas pelos dados da
resposta (status "dry_run"/"success"/"error"), não pelo Response.status.
"""
import os
import sys
from pyrevit import forms, revit

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import approval
from revit_mcp import pdf_export
from revit_mcp import ribbon_reports as reports

TITLE = u"Emitir PDF"
ALL_SHEETS = u"Todas as folhas"
SOME_SHEETS = u"Escolher folhas..."


def chosen_sheets(doc):
    """{} for all sheets, {"sheets": [...]} for a selection, None if cancelled."""
    choice = forms.CommandSwitchWindow.show([ALL_SHEETS, SOME_SHEETS],
                                            message=u"Quais folhas emitir?")
    if not choice:
        return None
    if choice == ALL_SHEETS:
        return {}
    sheets = forms.select_sheets(title=u"Folhas para emitir", button_name=u"Emitir",
                                 include_placeholder=False, use_selection=False, doc=doc)
    if not sheets:
        return None
    return {"sheets": [sheet.SheetNumber for sheet in sheets]}


def show_error(response):
    forms.alert(reports.pdf_error_text(getattr(response, "data", None),
                                       getattr(response, "status", None)), title=TITLE)


def main():
    doc = revit.doc
    if doc is None:
        forms.alert(u"Abra um projeto no Revit primeiro.", title=TITLE)
        return
    data = chosen_sheets(doc)
    if data is None:
        return

    # The dry run never calls approve; refusing everything is the safe default.
    plan = pdf_export.issue_pdfs(doc, dict(data), reports.replacement_guard([]))
    if not reports.is_plan(plan.data):
        show_error(plan)
        return
    files = plan.data.get("files") or []
    if not plan.data.get("combine") and len(files) > pdf_export.MAX_EXPORT_FILES:
        forms.alert(u"{} PDFs planejados; emita no máximo {} por vez.".format(
            len(files), pdf_export.MAX_EXPORT_FILES),
            sub_msg=u"Use 'Escolher folhas...' e divida a emissão.", title=TITLE)
        return
    message, details = reports.pdf_plan_text(plan.data)
    if not forms.alert(message, sub_msg=details, title=TITLE, ok=False, yes=True, no=True):
        return
    existing = reports.existing_files(plan.data)
    if existing:
        message, details = reports.pdf_replace_text(existing)
        if not forms.alert(message, sub_msg=details, title=TITLE, ok=False, yes=True, no=True):
            return

    folder = plan.data["folder"]
    run = dict(data)
    run.update({"dry_run": False, "folder": folder, "issued_at": plan.data["issued_at"],
                "overwrite": reports.needs_overwrite(plan.data)})
    result = pdf_export.issue_pdfs(doc, run, reports.replacement_guard(
        reports.replace_paths(plan.data)))
    if not reports.is_run_result(result.data):
        show_error(result)
        return
    approval.record(u"Emitir PDF (botão)", u"usuário no Revit",
                    reports.pdf_history_summary(result.data))
    message, details, _ = reports.pdf_result_text(result.data)
    if result.data.get("exported") and os.path.isdir(folder):
        if forms.alert(message, sub_msg=details + u"\n\nAbrir a pasta?", title=TITLE,
                       ok=False, yes=True, no=True):
            os.startfile(folder)
    else:
        forms.alert(message, sub_msg=details, title=TITLE)


try:
    main()
except Exception as error:
    forms.alert(u"A emissão falhou.", sub_msg=u"{}".format(error), title=TITLE)
