# -*- coding: utf-8 -*-
"""Auditar template (sem IA): a mesma auditoria da tool audit_template,
com todas as seções. Só leitura; oferece salvar o relatório como planilha
Excel (.xlsx, uma aba por seção) e como página HTML (para ler no navegador ou
imprimir em PDF), lado a lado na pasta escolhida."""
import datetime
import os
import sys
from pyrevit import forms, revit, script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import audit
from revit_mcp import audit_export
from revit_mcp import ribbon_reports as reports
from revit_mcp.utils import element_id_from_int

TITLE = u"Auditar template"
LOCKED = u"Feche a planilha no Excel e tente de novo."


def choose_path(doc, today):
    """The .xlsx path chosen in the save dialog, or None if cancelled. Asks
    before replacing files the dialog did not ask about (the .html beside it)."""
    folder = u""
    try:
        if doc.PathName:
            folder = os.path.dirname(doc.PathName)
    except Exception:
        pass
    name = audit_export.export_file_name(doc.Title, today)
    path = forms.save_file(file_ext="xlsx", default_name=name, init_dir=folder,
                           title=u"Salvar a auditoria (planilha Excel; a página HTML vai junto)")
    if not path:
        return None
    existing = audit_export.existing_outputs(path)
    if existing and not forms.alert(
            u"Substituir os arquivos que já existem?",
            sub_msg=u"\n".join(existing), title=TITLE, ok=False, yes=True, no=True):
        return None
    return audit_export.xlsx_path_for(path)


def save_error(message, error):
    detail = LOCKED if audit_export.is_locked_error(error) else u"{}".format(error)
    forms.alert(message, sub_msg=detail, title=TITLE)


def main():
    doc = revit.doc
    if doc is None:
        forms.alert(u"Abra um projeto ou template no Revit primeiro.", title=TITLE)
        return
    options, error = audit.parse_request({"limit": audit.MAX_LIMIT})
    if error:
        forms.alert(error, title=TITLE)
        return
    try:
        report = audit.build_audit(doc, options)
    except Exception as error:
        forms.alert(u"A auditoria falhou.", sub_msg=u"{}".format(error), title=TITLE)
        return
    output = script.get_output()
    output.set_title(TITLE)
    reports.render(output, reports.audit_report(
        report, link=lambda value: output.linkify(element_id_from_int(value))))
    if not forms.alert(u"Salvar o relatório (planilha Excel e página HTML)?",
                       sub_msg=u"A planilha tem uma aba por seção, com todos os itens. "
                               u"A página HTML, com o mesmo nome, fica ao lado dela: abre no "
                               u"navegador e pode ser impressa em PDF. Se já existir uma "
                               u"página com esse nome, ela será substituída.",
                       title=TITLE, ok=False, yes=True, no=True):
        return
    today = datetime.date.today().isoformat()
    path = choose_path(doc, today)
    if not path:
        return
    try:
        xlsx_path, html_path = audit_export.write_audit_files(path, report, today)
    except audit_export.HtmlSaveError as error:
        output.print_md(u"Planilha salva em `{}`".format(error.xlsx_path))
        save_error(u"A planilha foi salva, mas a página HTML não.", error.error)
        return
    except Exception as error:
        save_error(u"Não foi possível salvar o relatório.", error)
        return
    output.print_md(u"Planilha salva em `{}`".format(xlsx_path))
    output.print_md(u"Página HTML salva em `{}`".format(html_path))
    folder = os.path.dirname(xlsx_path)
    if os.path.isdir(folder) and forms.alert(
            u"Relatório salvo.", sub_msg=u"{}\n{}\n\nAbrir a pasta?".format(xlsx_path, html_path),
            title=TITLE, ok=False, yes=True, no=True):
        try:
            os.startfile(folder)
        except Exception as error:
            forms.alert(u"Não foi possível abrir a pasta.",
                        sub_msg=u"{}\n{}".format(folder, error), title=TITLE)

main()
