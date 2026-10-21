"""pyRevit refuses to load a button whose bundle.yaml is invalid YAML."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_bundle_values_with_colon_space_are_quoted():
    # An unquoted ": " inside a value is read as a nested mapping (pyRevit:
    # "found invalid mapping") and the whole button fails to load.
    bad = []
    for path in ROOT.glob("MCP.tab/**/bundle.yaml"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = re.match(r"^\s*[\w-]+:\s+(.*)$", line)
            if not match:
                continue
            value = match.group(1)
            if value[:1] in "\"'|>" or not value:
                continue
            if ": " in value or value.endswith(":") or " #" in value:
                bad.append("{}:{}".format(path.relative_to(ROOT), number))
    assert not bad, "Quote these bundle.yaml values: " + ", ".join(bad)
