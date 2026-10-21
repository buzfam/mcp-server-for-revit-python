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


def decision(choice):
    """Map the dialog choice to (allowed, enable_session_approval)."""
    if choice == ALLOW_SESSION:
        return True, True
    return choice == ALLOW, False
