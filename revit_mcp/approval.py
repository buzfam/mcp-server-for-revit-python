# -*- coding: utf-8 -*-
"""Session-wide approval of MCP write requests, chosen in Revit by the local user.

Kept in the AppDomain (memory of the Revit process), never on disk: it ends
when Revit closes. Only the review dialog and the ribbon Status button set it,
so a remote client cannot turn it on.
"""
KEY = "revit_mcp.auto_approve"

ALLOW = u"Permitir"
ALLOW_SESSION = u"Permitir tudo até fechar o Revit"
DECLINE = u"Recusar"
OPTIONS = [ALLOW, ALLOW_SESSION, DECLINE]


def _domain():
    from System import AppDomain
    return AppDomain.CurrentDomain


def is_auto_approved():
    try:
        return bool(_domain().GetData(KEY))
    except Exception:
        return False


def set_auto_approved(enabled):
    _domain().SetData(KEY, True if enabled else None)


HISTORY_KEY = "revit_mcp.approval_history"
HISTORY_LIMIT = 200
_local_history = []  # used when there is no .NET AppDomain (tests, CPython)

AUTO = u"automático (sessão)"
DECLINED = u"recusado"


def outcome_text(choice):
    """Readable outcome of a dialog choice for the history."""
    if choice == ALLOW_SESSION:
        return u"permitido + sessão inteira"
    if choice == ALLOW:
        return u"permitido"
    return DECLINED


def request_summary(data, max_keys=4, max_text=40):
    """Short description of a request payload: list sizes and short values."""
    if not isinstance(data, dict):
        return u""
    parts = []
    for key in sorted(data, key=lambda k: u"{}".format(k))[:max_keys]:
        value = data[key]
        if isinstance(value, (list, tuple)):
            text = u"{} itens".format(len(value))
        elif isinstance(value, dict):
            text = u"{...}"
        else:
            text = u"{}".format(value).replace(u"\n", u" ")
            if len(text) > max_text:
                text = text[:max_text - 3] + u"..."
        parts.append(u"{}={}".format(key, text))
    if len(data) > max_keys:
        parts.append(u"+{}".format(len(data) - max_keys))
    return u", ".join(parts)


def _history_store():
    try:
        domain = _domain()
    except Exception:
        return _local_history
    store = domain.GetData(HISTORY_KEY)
    if store is None:
        from System.Collections.Generic import List
        from System import String
        store = List[String]()
        domain.SetData(HISTORY_KEY, store)
    return store


def record(action, outcome, summary=u""):
    """Remember one reviewed request until Revit closes (newest last)."""
    import datetime
    line = u"{}  {}  {}  {}".format(datetime.datetime.now().strftime("%H:%M:%S"),
                                    outcome, action, summary).rstrip()
    try:
        store = _history_store()
        store.Add(line) if hasattr(store, "Add") else store.append(line)
        while len(store) > HISTORY_LIMIT:
            store.RemoveAt(0) if hasattr(store, "RemoveAt") else store.pop(0)
    except Exception:
        pass
    try:
        import logging
        logging.getLogger("revit_mcp.approval").info(line)
    except Exception:
        pass
    return line


def history():
    """Reviewed requests of this Revit session, newest first."""
    try:
        return list(reversed([u"{}".format(line) for line in _history_store()]))
    except Exception:
        return []


def decision(choice):
    """Map the dialog choice to (allowed, enable_session_approval)."""
    if choice == ALLOW_SESSION:
        return True, True
    return choice == ALLOW, False
