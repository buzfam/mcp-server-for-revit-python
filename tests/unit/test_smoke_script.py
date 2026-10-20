# -*- coding: utf-8 -*-
"""Pure parts of scripts/smoke.py (the live run needs Revit)."""
import pytest

from scripts import smoke


@pytest.mark.parametrize("text, status", [
    ('{"status": "success"}', smoke.PASS),
    ("=== REVIT STATUS ===\nStatus: active", smoke.PASS),
    ("Error: Request timed out after 30 seconds.", smoke.FAIL),
    ("=== ERROR DETAILS ===\nStatus: unknown\nError: boom", smoke.FAIL),
    ("Error: Use Revit's MCP > Bridge > Connect button to consent and pair this Windows user.",
     smoke.FAIL),
])
def test_classify(text, status):
    assert smoke.classify(text)[0] == status


def test_classify_explains_missing_pairing():
    status, detail = smoke.classify("Error: Use Revit's MCP > Bridge > Connect button ...")
    assert "Connect" in detail


def test_parse_args_remote_needs_token(monkeypatch):
    monkeypatch.delenv("REVIT_MCP_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        smoke.parse_args(["--url", "http://10.0.0.1:8000/mcp"])


def test_parse_args_token_from_env(monkeypatch):
    monkeypatch.setenv("REVIT_MCP_TOKEN", "t")
    assert smoke.parse_args(["--url", "http://10.0.0.1:8000/mcp"]).token == "t"


def test_parse_args_requires_a_target():
    with pytest.raises(SystemExit):
        smoke.parse_args([])


def test_report_summary_and_failure():
    report = smoke.Report()
    report.add("a", smoke.PASS)
    report.add("b", smoke.WARN)
    assert not report.failed()
    report.add("c", smoke.FAIL)
    assert report.failed()
    assert report.summary() == {"PASS": 1, "WARN": 1, "SKIP": 0, "FAIL": 1}
