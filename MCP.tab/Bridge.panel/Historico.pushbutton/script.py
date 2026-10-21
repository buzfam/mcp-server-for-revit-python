# -*- coding: utf-8 -*-
"""Mostra os pedidos de alteração revisados nesta sessão do Revit."""
import os
import sys
from pyrevit import script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import approval, log_path

output = script.get_output()
output.print_md(u"### Pedidos do agente nesta sessão (mais recentes primeiro)")
if approval.is_auto_approved():
    output.print_md(u"**Aprovação automática LIGADA** até fechar o Revit "
                    u"(desligue em Connected > Voltar a pedir aprovação).")
lines = approval.history()
print(u"\n".join(lines) if lines else u"(nenhum pedido de alteração nesta sessão)")
output.print_md(u"Histórico completo, inclusive de sessões anteriores: `{}`".format(log_path()))
