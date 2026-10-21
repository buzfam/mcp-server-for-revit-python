# -*- coding: utf-8 -*-
"""Pure helpers for the ribbon Atualizar button (no Revit or pyRevit imports)."""
import os


def installed_commit(root):
    """Commit SHA checked out in a git working tree, read from .git directly."""
    git_dir = os.path.join(root, ".git")
    try:
        with open(os.path.join(git_dir, "HEAD")) as stream:
            head = stream.read().strip()
        if not head.startswith("ref:"):
            return head
        ref = head[4:].strip()
        ref_path = os.path.join(git_dir, *ref.split("/"))
        if os.path.isfile(ref_path):
            with open(ref_path) as stream:
                return stream.read().strip()
        with open(os.path.join(git_dir, "packed-refs")) as stream:
            for line in stream:
                parts = line.strip().split(" ")
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except (IOError, OSError):
        pass
    return None


def server_is_stale(state, commit):
    """True when the running remote server was started from another commit.

    Servers started before the commit was recorded count as stale too.
    """
    if not state or not commit:
        return False
    return state.get("commit") != commit


def short_hash(sha):
    return (sha or "")[:7]


def commit_lines(messages, limit=10):
    """One bullet per commit (first line of each message), newest first."""
    lines = []
    for message in messages[:limit]:
        first = (message or u"").strip().splitlines()
        lines.append(u"• " + (first[0] if first else u"(sem mensagem)"))
    if len(messages) > limit:
        lines.append(u"• ... e mais {}".format(len(messages) - limit))
    return u"\n".join(lines)


def result_message(old_sha, new_sha, server_was_running, server_ready, server_detail):
    text = u"Atualizado de {} para {}.".format(short_hash(old_sha), short_hash(new_sha))
    if server_was_running:
        if server_ready:
            text += u"\nServidor remoto reiniciado com a versão nova."
        else:
            text += (u"\nO servidor remoto ainda está iniciando ({}). Confira em "
                     u"Remoto > Ver log do servidor.".format(server_detail))
    text += u"\n\nO pyRevit vai recarregar agora."
    return text
