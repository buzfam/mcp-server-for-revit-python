# -*- coding: utf-8 -*-
"""Pure helpers for the ribbon Atualizar button (no Revit or pyRevit imports)."""


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
