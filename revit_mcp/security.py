# -*- coding: utf-8 -*-
"""Authentication and in-Revit review for bridge requests."""
import json
from pyrevit import routes
from .pairing import AUTH_FIELD, validate_request

_TOKEN_ATTRIBUTE = "_revit_mcp_body_token"


def _pop_body_token(request):
    """Remove the pairing secret from the payload so routes and review never see it.

    The token is kept on the request because a route may authorize twice
    (authenticate first, then show the review dialog for a real write).
    """
    data = request.data
    if data and not isinstance(data, dict):
        try:
            parsed = json.loads(data)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            request.data = data = parsed
    if isinstance(data, dict) and AUTH_FIELD in data:
        setattr(request, _TOKEN_ATTRIBUTE, data.pop(AUTH_FIELD))
    return getattr(request, _TOKEN_ATTRIBUTE, None)


try:
    _NUMBERS = (int, long, float)  # IronPython 2: big integers are long
except NameError:
    _NUMBERS = (int, float)


def _quote(text):
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return u'"' + escaped + u'"'


def preview_text(data, indent=0):
    """JSON-like text of a request for the review dialog.

    json.dumps cannot be used: on IronPython 2 it raises UnicodeEncodeError for
    accented text ("Comentários"), which turned every accented write into a 400.
    """
    pad = u"  " * (indent + 1)
    end = u"  " * indent
    if isinstance(data, dict):
        if not data:
            return u"{}"
        items = [pad + _quote(u"{}".format(key)) + u": " + preview_text(value, indent + 1)
                 for key, value in sorted(data.items(), key=lambda item: u"{}".format(item[0]))]
        return u"{\n" + u",\n".join(items) + u"\n" + end + u"}"
    if isinstance(data, (list, tuple)):
        if not data:
            return u"[]"
        return u"[\n" + u",\n".join(pad + preview_text(value, indent + 1) for value in data) + \
            u"\n" + end + u"]"
    if data is None:
        return u"null"
    if data is True or data is False:
        return u"true" if data else u"false"
    if isinstance(data, _NUMBERS):
        return u"{}".format(data)
    return _quote(u"{}".format(data))


def authorize_request(request, action=None):
    """Return a denial response, or None when the request may proceed."""
    body_token = _pop_body_token(request)
    # pyRevit's dispatcher can post a route response to callbackUrl. Remove it
    # before returning so even a denial cannot initiate an undeclared request.
    if isinstance(request.data, dict) and "callbackUrl" in request.data:
        request.data.pop("callbackUrl", None)
        return routes.make_response(data={"error": "Callbacks are not supported."}, status=400)
    if not validate_request(request.headers, body_token):
        return routes.make_response(data={"error": "Bridge is disconnected or pairing is invalid."}, status=401)
    if action:
        from pyrevit import forms
        try:
            data = json.loads(request.data) if isinstance(request.data, str) else request.data
            preview = preview_text(data)
        except (ValueError, TypeError):
            return routes.make_response(data={"error": "Invalid request payload."}, status=400)
        warning = "An MCP client requests: {}.\nReview the full request below before allowing it.".format(action)
        if action == "Execute Python code":
            warning += "\nThis code is NOT sandboxed. It can access files, network services and any data available to your Windows account."
        if not forms.alert(warning, sub_msg=preview, title="Revit MCP request review",
                           ok=False, yes=True, no=True, warn_icon=True):
            return routes.make_response(data={"error": "The user declined the request in Revit."}, status=403)
    return None
