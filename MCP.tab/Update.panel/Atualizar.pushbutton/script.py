# -*- coding: utf-8 -*-
"""Update this extension from git, restart the remote server and reload pyRevit."""
import os
import sys
from pyrevit import forms
from pyrevit.coreutils import git as libgit
from pyrevit.versionmgr import updater

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import remote_server as server
from revit_mcp.self_update import commit_lines, result_message, short_hash

TITLE = "Atualizar Revit MCP"


def find_repo():
    repo_path = libgit.libgit.Repository.Discover(extension_root)
    return libgit.get_repo(repo_path) if repo_path else None


def new_commit_messages(repo_info):
    try:
        return [commit for commit in libgit.get_all_new_commits(repo_info).values()]
    except Exception:
        return []


def restart_server():
    """Restart the remote server if it was running, so it runs the new main.py."""
    if not server.is_running():
        return False, False, ""
    port = (server.read_state() or {}).get("port", server.DEFAULT_PORT)
    server.stop()
    ready, detail = server.start(port)
    return True, ready, detail


def main():
    repo_info = find_repo()
    if repo_info is None:
        forms.alert(u"Esta extensão não foi instalada pelo git, então não dá para "
                    u"atualizar por aqui.", sub_msg=u"Use pyRevit > Extensions.", title=TITLE)
        return
    old_sha = repo_info.last_commit_hash
    try:
        pending = updater.has_pending_updates(repo_info)
    except Exception as error:
        forms.alert(u"Não foi possível verificar atualizações.", sub_msg=str(error), title=TITLE)
        return
    if not pending:
        forms.alert(u"Já está na versão mais recente ({}).".format(short_hash(old_sha)),
                    title=TITLE)
        return

    changes = commit_lines(new_commit_messages(repo_info))
    if not forms.alert(u"Há uma atualização. Instalar agora?",
                       sub_msg=(changes + u"\n\n" if changes else u"") +
                       u"O servidor remoto reinicia (se estiver ligado) e o pyRevit recarrega.",
                       title=TITLE, ok=False, yes=True, no=True):
        return
    try:
        new_sha = updater.update_repo(repo_info).last_commit_hash
    except Exception as error:
        forms.alert(u"A atualização falhou; nada foi alterado.", sub_msg=str(error), title=TITLE)
        return

    was_running, ready, detail = restart_server()
    forms.alert(result_message(old_sha, new_sha, was_running, ready, detail), title=TITLE)

    from pyrevit.loader import sessionmgr
    sessionmgr.reload_pyrevit()


main()
