# -*- coding: utf-8 -*-
"""Auditar template (sem IA): a mesma auditoria da tool audit_template,
com todas as seções. Só leitura; oferece salvar o relatório completo em JSON
ao lado do modelo."""
import datetime
import os
import sys
from pyrevit import forms, revit, script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import audit
from revit_mcp import pdf_export
from revit_mcp import ribbon_reports as reports
from revit_mcp.utils import element_id_from_int

TITLE = u"Auditar template"


def save_json(doc, report):
    folder = u""
    try:
        if doc.PathName:
            folder = os.path.dirname(doc.PathName)
    except Exception:
        pass
    name = reports.audit_file_name(doc.Title, datetime.date.today().isoformat())
    path = forms.save_file(file_ext="json", default_name=name, init_dir=folder,
                           title=u"Salvar a auditoria em JSON")
    if not path:
        return None
    # json_text is ASCII-safe; json.dumps fails on accented names in IronPython.
    pdf_export.write_text_file(path, pdf_export.json_text(report) + u"\n")
    return path


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
    if forms.alert(u"Salvar o relatório completo em JSON?",
                   sub_msg=u"Útil para a planilha de auditoria ou para comparar depois.",
                   title=TITLE, ok=False, yes=True, no=True):
        try:
            path = save_json(doc, report)
        except Exception as error:
            forms.alert(u"Não foi possível salvar o JSON.", sub_msg=u"{}".format(error), title=TITLE)
            return
        if path:
            output.print_md(u"Relatório salvo em `{}`".format(path))


main()
