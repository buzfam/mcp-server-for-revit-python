# -*- coding: utf-8 -*-
"""Emitir PDF (sem IA): a mesma emissão da tool export_sheets_pdf.

1. Escolher as folhas (todas ou algumas) e onde salvar (pasta padrão, a
   última usada ou outra, inclusive no servidor da rede).
2. Plano (dry run): pasta, nomes dos arquivos e avisos; confirmar.
3. Se algum arquivo já existe, confirmar a substituição (o antigo vira .bak
   até o novo ser gravado e volta se a exportação falhar).
4. Exportar, mostrar o resultado e oferecer abrir a pasta.
Quem clica é quem aprova, por isso não há o diálogo de aprovação do MCP; a
emissão fica registrada no Histórico. As decisões são tomadas pelos dados da
resposta (status "dry_run"/"success"/"error"), não pelo Response.status.
"""
import io
import json
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
DEFAULT_FOLDER = u"Pasta padrão (Documentos\\RevitMCP\\Emissoes, uma pasta por emissão)"
PICK_FOLDER = u"Escolher outra pasta..."
LAST_FOLDER = u"Última pasta: {}"


def settings_path():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                        "RevitMCPPython", "emitir_pdf.json")


def last_folder():
    try:
        with io.open(settings_path(), encoding="utf-8") as stream:
            folder = json.loads(stream.read()).get("last_folder")
        return folder if folder and os.path.isdir(folder) else None
    except Exception:
        return None


def remember_folder(folder):
    try:
        path = settings_path()
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        pdf_export.write_text_file(path, pdf_export.json_text({"last_folder": folder}) + u"\n")
    except Exception:
        pass


def chosen_folder():
    """None for the default dated folder, a path, or False if cancelled."""
    previous = last_folder()
    options = [DEFAULT_FOLDER]
    if previous:
        options.append(LAST_FOLDER.format(previous))
    options.append(PICK_FOLDER)
    choice = forms.CommandSwitchWindow.show(options, message=u"Onde salvar os PDFs?")
    if not choice:
        return False
    if choice == DEFAULT_FOLDER:
        return None
    if previous and choice == LAST_FOLDER.format(previous):
        return previous
    folder = forms.pick_folder(title=u"Pasta para os PDFs")
    return folder or False


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
    folder = chosen_folder()
    if folder is False:
        return
    if folder:
        data["folder"] = folder

    # The dry run never calls approve; refusing everything is the safe default.
    plan = pdf_export.issue_pdfs(doc, dict(data), reports.replacement_guard([]),
                                 local_user=True)
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
        reports.replace_paths(plan.data)), local_user=True)
    if not reports.is_run_result(result.data):
        show_error(result)
        return
    if data.get("folder"):
        remember_folder(data["folder"])
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
