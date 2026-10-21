# -*- coding: utf-8 -*-
"""Checar pranchas (sem IA): o mesmo checklist da tool check_sheets.

Clique: usa %APPDATA%\\RevitMCPPython\\checklists_pranchas.json (fora da extensão,
para o botão Atualizar não sobrescrever); no primeiro uso ele é copiado de
<extensão>/config/checklists_pranchas.exemplo.json.
Shift+clique: escolhe outro arquivo JSON de checklists.
Só leitura: não abre transação.
"""
import io
import os
import sys
from pyrevit import forms, revit, script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import ribbon_reports as reports
from revit_mcp import sheet_checks
from revit_mcp.utils import element_id_from_int

TITLE = u"Checar pranchas"

try:
    SHIFT = __shiftclick__  # noqa: F821 - set by pyRevit
except NameError:
    SHIFT = False


def appdata_folder():
    try:
        from System import Environment
        folder = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData)
        if folder:
            return folder
    except Exception:
        pass
    return os.environ.get("APPDATA")


def checklists_file(output):
    """Path of the checklists (None if cancelled). Shift+click: picker; else the office file in APPDATA."""
    user, example = reports.checklists_paths(appdata_folder(), extension_root)
    if SHIFT:
        folder = os.path.dirname(user) if user and os.path.isdir(os.path.dirname(user)) \
            else os.path.dirname(example)
        return forms.pick_file(file_ext="json", init_dir=folder,
                               title=u"Arquivo de checklists das pranchas")
    path, created, error = reports.prepare_checklists(appdata_folder(), extension_root)
    if error:
        forms.alert(u"Não foi possível preparar o arquivo de checklists.", sub_msg=error,
                    title=TITLE)
        return None
    if created:
        output.print_md(u"**Primeiro uso:** os checklists de EXEMPLO foram copiados para "
                        u"`{}`. Edite esse arquivo com os checklists do escritório "
                        u"(formato na spec 013); o botão Atualizar não mexe nele.".format(path))
    return path


def main():
    doc = revit.doc
    if doc is None:
        forms.alert(u"Abra um projeto no Revit primeiro.", title=TITLE)
        return
    output = script.get_output()
    output.set_title(TITLE)
    path = checklists_file(output)
    if not path:
        return
    try:
        with io.open(path, encoding="utf-8-sig") as stream:
            raw = stream.read()
    except (IOError, OSError) as error:
        forms.alert(u"Não foi possível ler o arquivo de checklists.", sub_msg=u"{}".format(error),
                    title=TITLE)
        return
    body, error = reports.load_checklists(raw)
    if error:
        forms.alert(u"Arquivo de checklists inválido:\n{}".format(path), sub_msg=error, title=TITLE)
        return
    options, error, errors = sheet_checks.parse_options(body)
    if error:
        forms.alert(u"Arquivo de checklists inválido:\n{}".format(path),
                    sub_msg=reports.checklist_errors_text(error, errors), title=TITLE)
        return
    try:
        result = sheet_checks.check_sheets(doc, options)
    except Exception as error:
        forms.alert(u"A checagem falhou.", sub_msg=u"{}".format(error), title=TITLE)
        return
    reports.render(output, reports.sheet_report(
        result, source=path, link=lambda value: output.linkify(element_id_from_int(value))))


main()
