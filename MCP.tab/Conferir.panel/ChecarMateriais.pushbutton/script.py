# -*- coding: utf-8 -*-
"""Checar materiais (sem IA): a mesma regra da tool check_render_materials.

Clique: mobiliário e afins do modelo inteiro, com as opções padrão.
Shift+clique: só o que aparece na vista ativa.
Só leitura: não abre transação.
"""
import os
import sys
from pyrevit import forms, revit, script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import material_checks
from revit_mcp import ribbon_reports as reports
from revit_mcp.utils import element_id_from_int, element_id_value

TITLE = u"Checar materiais"

try:
    SHIFT = __shiftclick__  # noqa: F821 - set by pyRevit
except NameError:
    SHIFT = False


def main():
    doc = revit.doc
    if doc is None:
        forms.alert(u"Abra um projeto no Revit primeiro.", title=TITLE)
        return
    data = {"limit": material_checks.MAX_LIMIT}
    if SHIFT:
        view = revit.active_view
        if view is None:
            forms.alert(u"Abra uma vista 3D, planta ou elevação e tente de novo.", title=TITLE)
            return
        refusal = reports.material_view_error(str(view.ViewType), view.IsTemplate)
        if refusal:
            forms.alert(refusal, title=TITLE)
            return
        data["only_visible_in_view"] = element_id_value(view.Id)
    try:
        result, error, status = material_checks.run_material_check(doc, data)
    except Exception as error:
        forms.alert(u"A checagem falhou.", sub_msg=u"{}".format(error), title=TITLE)
        return
    if error:
        forms.alert(u"A checagem não foi feita.", sub_msg=u"{} ({})".format(error, status),
                    title=TITLE)
        return
    output = script.get_output()
    output.set_title(TITLE)
    reports.render(output, reports.material_report(
        result, link=lambda value: output.linkify(element_id_from_int(value))))


main()
