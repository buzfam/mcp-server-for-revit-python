# -*- coding: utf-8 -*-
"""Every revit_mcp module with a register_*_routes function is registered in startup.py."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _route_modules():
    tree = ast.parse((ROOT / "startup.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "ROUTE_MODULES":
            return [tuple(item.value for item in pair.elts) for pair in node.value.elts]
    raise AssertionError("ROUTE_MODULES not found in startup.py")


def test_every_route_module_is_registered():
    registered = dict(_route_modules())
    for path in sorted((ROOT / "revit_mcp").glob("*.py")):
        functions = re.findall(r"^def (register_\w+_routes)\(", path.read_text(encoding="utf-8"), re.M)
        for function in functions:
            module = "revit_mcp." + path.stem
            assert registered.get(module) == function, "{}.{} missing from startup.ROUTE_MODULES".format(
                module, function)


def test_registered_functions_exist():
    for module, function in _route_modules():
        source = (ROOT / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")
        assert "def {}(".format(function) in source, "{} has no {}".format(module, function)
