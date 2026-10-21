# -*- coding: utf-8 -*-
"""Show the MCP pairing state on the ribbon and connect or disconnect it.

One button replaces Connect/Disconnect: its title reads Connected or
Disconnected, so the user always sees the current state before clicking.
"""
import os
import sys
from pyrevit import forms, script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp.pairing import PairingError, disable_pairing, enable_pairing, is_paired

TITLE = "Revit MCP"
DISCONNECT_OPTION = u"Desconectar"
ROTATE_OPTION = u"Trocar o segredo"


def _state(paired):
    return ("Connected", "on.png") if paired else ("Disconnected", "off.png")


def _apply(button, folder, paired):
    from pyrevit.coreutils.ribbon import ICON_LARGE
    title, icon = _state(paired)
    button.set_title(title)
    button.set_icon(os.path.join(folder, icon), icon_size=ICON_LARGE)


def __selfinit__(script_cmp, ui_button_cmp, __rvt__):
    # Runs when pyRevit loads the ribbon; never block loading over the state.
    try:
        _apply(ui_button_cmp, script_cmp.directory, is_paired())
    except Exception:
        pass


def _refresh():
    folder = os.path.dirname(__file__)
    paired = is_paired()
    for button in script.get_all_buttons():
        try:
            _apply(button, folder, paired)
        except Exception:
            pass


def _connect():
    # Short question in the main text; the details go in sub_msg so the dialog
    # keeps a normal size instead of stretching around one long paragraph.
    details = (
        u"• Eles leem dados do modelo: nomes do projeto e do cliente, caminhos, "
        u"parâmetros, ambientes, vínculos e imagens de vistas.\n"
        u"• O cliente de IA pode enviar essas respostas ao provedor dele. Use só "
        u"modelos que você pode compartilhar.\n"
        u"• Alterar o modelo ou executar Python sempre pede uma nova confirmação "
        u"aqui no Revit. O Python não roda isolado.\n"
        u"• Outros programas rodando com o seu usuário também usam este pareamento.\n"
        u"• Vale até você desconectar neste mesmo botão.\n\n"
        u"Mais detalhes no botão Privacy."
    )
    if not forms.alert(u"Permitir que clientes MCP deste usuário do Windows acessem este Revit?",
                       sub_msg=details, title=u"Conectar Revit MCP", ok=False, yes=True, no=True):
        return
    enable_pairing()
    forms.alert(u"Connected.", sub_msg=u"Se o pyRevit Routes não estiver ligado, "
                u"ligue em pyRevit > Settings > Routes.", title=TITLE)


def _manage():
    choice = forms.alert(
        u"Connected: clientes MCP deste usuário do Windows podem acessar este Revit.",
        sub_msg=u"Desconectar recusa os próximos pedidos (uma operação em andamento "
                u"ainda pode terminar). Trocar o segredo invalida o antigo; o servidor "
                u"remoto continua funcionando sem reiniciar.",
        title=TITLE, options=[DISCONNECT_OPTION, ROTATE_OPTION])
    if choice == DISCONNECT_OPTION:
        disable_pairing()
        forms.alert(u"Disconnected. Novos pedidos do MCP serão recusados.", title=TITLE)
    elif choice == ROTATE_OPTION:
        enable_pairing()
        forms.alert(u"Segredo trocado. Continua Connected.", title=TITLE)


if __name__ == "__main__":
    try:
        if is_paired():
            _manage()
        else:
            _connect()
    except (PairingError, OSError, IOError) as error:
        forms.alert(str(error), title=u"Revit MCP: falha no pareamento")
    finally:
        _refresh()
