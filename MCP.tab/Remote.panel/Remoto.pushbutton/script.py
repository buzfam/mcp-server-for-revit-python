# -*- coding: utf-8 -*-
"""Acesso remoto: ver e renovar o token, iniciar e parar o servidor de rede."""
import os
import sys
from pyrevit import forms, script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import remote_server as server
from revit_mcp.pairing import get_token, PairingError
from revit_mcp.remote_token import (
    RemoteAccessError,
    create_remote_token,
    get_remote_token,
    has_remote_token,
    revoke_remote_token,
)

TITLE = "Revit MCP - Acesso remoto"

SHOW = "Ver token e comando de conexão"
NEW = "Gerar novo token"
CREATE = "Criar token"
REVOKE = "Revogar token"
START = "Iniciar servidor remoto"
STOP = "Parar servidor remoto"
LOG = "Ver log do servidor"


def copy(text):
    script.clipboard_copy(text)
    forms.alert("Copiado para a área de transferência.", title=TITLE, warn_icon=False)


def current_port():
    return (server.read_state() or {}).get("port", server.DEFAULT_PORT)


def show_connection(token):
    port = current_port()
    ips = server.local_ipv4_addresses()
    name = server.client_name(server.machine_name())
    ip = ips[0] if ips else "<IP-DO-PC>"
    command = server.connection_command(name, ip, port, token)
    message = (
        "Servidor: {}\nIPs deste PC: {}\nPorta: {}\n\n"
        "Header no agente:\nAuthorization: Bearer {}"
    ).format("RODANDO" if server.is_running() else "PARADO",
             ", ".join(ips) or "nenhum encontrado", port, token)
    choice = forms.alert(
        message, title=TITLE, warn_icon=False,
        sub_msg="Comando para o Claude Code na outra máquina:\n" + command,
        options=["Copiar comando do Claude Code", "Copiar token", "Fechar"])
    if choice == "Copiar comando do Claude Code":
        copy(command)
    elif choice == "Copiar token":
        copy(token)


def new_token(replacing):
    if replacing and not forms.alert(
            "Gerar um novo token? O token atual deixa de funcionar na hora e "
            "os agentes conectados precisarão do novo.",
            title=TITLE, ok=False, yes=True, no=True):
        return
    show_connection(create_remote_token())


def revoke():
    if forms.alert("Revogar o token? Nenhum agente remoto consegue se conectar até "
                   "você criar um novo.", title=TITLE, ok=False, yes=True, no=True):
        revoke_remote_token()
        forms.alert("Token revogado.", title=TITLE, warn_icon=False)


def ensure_uv():
    uv_path = server.find_uv()
    if uv_path:
        return uv_path
    if not forms.alert(
            "O servidor precisa do uv (gerenciador de Python da Astral), que não "
            "foi encontrado neste PC.\n\nInstalar agora? Será baixado de astral.sh "
            "e instalado só para o seu usuário.",
            title=TITLE, ok=False, yes=True, no=True):
        return None
    uv_path = server.install_uv()
    if not uv_path:
        forms.alert("Não foi possível instalar o uv. Instale manualmente: "
                    "https://docs.astral.sh/uv/", title=TITLE)
    return uv_path


def start():
    try:
        get_token()
    except PairingError:
        forms.alert("Faça o pareamento primeiro: MCP > Bridge > Connect.", title=TITLE)
        return
    if not has_remote_token():
        if not forms.alert("Ainda não existe token remoto. Criar agora?",
                           title=TITLE, ok=False, yes=True, no=True):
            return
        create_remote_token()
    if not forms.alert(
            "Iniciar o servidor de acesso remoto na porta {}?\n\n"
            "Agentes em outras máquinas da rede com o token poderão ler o modelo "
            "e pedir alterações. Alterações continuam passando pelo diálogo de "
            "aprovação aqui no Revit.\n\nSe o Windows perguntar sobre o firewall, "
            "permita apenas em redes privadas/de domínio.".format(server.DEFAULT_PORT),
            title=TITLE, ok=False, yes=True, no=True):
        return
    uv_path = ensure_uv()
    if not uv_path:
        return
    ready, detail = server.start(server.DEFAULT_PORT, uv_path=uv_path, wait_sec=25)
    if ready:
        show_connection(get_remote_token())
    elif detail == "still starting":
        forms.alert("O servidor está iniciando. Na primeira vez ele baixa as "
                    "dependências e pode levar alguns minutos.\n\nVolte em "
                    "Remoto > Ver token e comando de conexão para conferir.",
                    title=TITLE, warn_icon=False)
    else:
        forms.alert("O servidor não iniciou.", title=TITLE,
                    sub_msg=detail or "Sem detalhes no log.")


def stop():
    server.stop()
    forms.alert("Servidor remoto parado.", title=TITLE, warn_icon=False)


def show_log():
    output = script.get_output()
    output.print_md("### Log do servidor remoto\n`{}`".format(server.log_path()))
    print(server.log_tail(80) or "(vazio)")


options = []
if has_remote_token():
    options += [SHOW, NEW, REVOKE]
else:
    options.append(CREATE)
options.append(STOP if server.is_running() else START)
options.append(LOG)

selected = forms.CommandSwitchWindow.show(options, message="Acesso remoto ao Revit MCP")
try:
    if selected == SHOW:
        show_connection(get_remote_token())
    elif selected in (NEW, CREATE):
        new_token(replacing=selected == NEW)
    elif selected == REVOKE:
        revoke()
    elif selected == START:
        start()
    elif selected == STOP:
        stop()
    elif selected == LOG:
        show_log()
except (RemoteAccessError, PairingError, OSError, IOError) as error:
    forms.alert(str(error), title=TITLE)
