# -*- coding: utf-8 -*-
"""Pair the local MCP client after explicit data-access consent."""
import os
import sys
from pyrevit import forms

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp.pairing import enable_pairing, PairingError

# Short question in the main text; the details go in sub_msg so the dialog
# keeps a normal size instead of stretching around one long paragraph.
question = u"Permitir que clientes MCP deste usuário do Windows acessem este Revit?"
details = (
    u"\u2022 Eles leem dados do modelo: nomes do projeto e do cliente, caminhos, "
    u"parâmetros, ambientes, vínculos e imagens de vistas.\n"
    u"\u2022 O cliente de IA pode enviar essas respostas ao provedor dele. Use só "
    u"modelos que você pode compartilhar.\n"
    u"\u2022 Alterar o modelo ou executar Python sempre pede uma nova confirmação "
    u"aqui no Revit. O Python não roda isolado.\n"
    u"\u2022 Outros programas rodando com o seu usuário também usam este pareamento.\n"
    u"\u2022 Vale até você clicar em Disconnect. Clicar em Connect de novo troca o segredo.\n\n"
    u"Mais detalhes no botão Privacy."
)
if forms.alert(question, sub_msg=details, title="Conectar Revit MCP", ok=False, yes=True, no=True):
    try:
        enable_pairing()
        forms.alert(u"Pareado. Se o pyRevit Routes não estiver ligado, ligue em pyRevit > Settings > Routes.",
                    title="Revit MCP")
    except (PairingError, OSError) as error:
        forms.alert(str(error), title="Revit MCP: falha no pareamento")
