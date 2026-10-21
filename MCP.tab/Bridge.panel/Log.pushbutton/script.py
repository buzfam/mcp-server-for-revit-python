# -*- coding: utf-8 -*-
"""Mostra o final do log do Revit MCP e do servidor remoto."""
import io
import os
import sys
from pyrevit import script

extension_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if extension_root not in sys.path:
    sys.path.insert(0, extension_root)
from revit_mcp import log_path
from revit_mcp import remote_server

LINES = 80


def tail(path, lines):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as stream:
            return u"".join(stream.readlines()[-lines:])
    except (IOError, OSError):
        return u""


output = script.get_output()
output.print_md(u"### Requisições do Revit MCP\n`{}`".format(log_path()))
print(tail(log_path(), LINES) or u"(vazio: nenhum erro registrado)")
output.print_md(u"### Servidor remoto\n`{}`".format(remote_server.log_path()))
print(tail(remote_server.log_path(), 30) or u"(vazio)")
