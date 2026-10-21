"""Homologation smoke test: call every read-only tool against a real Revit.

Remote (from another machine, Revit PC running the network server):
    uv run --frozen python scripts/smoke.py --url http://192.168.0.20:8000/mcp --token <TOKEN>

Local (on the Revit PC itself, stdio like Claude Desktop):
    uv run --frozen python scripts/smoke.py --local

Before running: open a model in Revit, pair (MCP > Bridge, the button shows Connected) and
select 1-3 elements so the selection/parameter tools have something to read.
Only read-only tools are called, plus bulk_set_parameters in dry-run mode
(validated inside a transaction that is rolled back); nothing is changed.
Writes a JSON report (default smoke-report.json) and exits 1 on any FAIL.
"""
import argparse
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from tools.catalog import TOOL_DESCRIPTIONS
from tools.registration import READ_ONLY

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {},
    "clientInfo": {"name": "revit-mcp-smoke", "version": "1.0"}}}
VIEW_TYPE_ORDER = ("floor_plans", "3d_views", "elevations", "sections",
                   "ceiling_plans", "drafting_views", "other")


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail="", args=None, seconds=None, excerpt=""):
        self.checks.append({"check": name, "status": status, "detail": detail,
                            "args": args, "seconds": seconds, "excerpt": excerpt})
        mark = {PASS: "ok  ", FAIL: "FAIL", WARN: "warn", SKIP: "skip"}[status]
        timing = " ({:.1f}s)".format(seconds) if seconds is not None else ""
        print("[{}] {}{}{}".format(mark, name, timing, " - " + detail if detail else ""))

    def failed(self):
        return any(c["status"] == FAIL for c in self.checks)

    def summary(self):
        counts = {s: 0 for s in (PASS, WARN, SKIP, FAIL)}
        for check in self.checks:
            counts[check["status"]] += 1
        return counts


def result_text(result):
    return "\n".join(getattr(item, "text", "") for item in result.content if hasattr(item, "text"))


def classify(text):
    """(status, detail) for a tool's text output."""
    if "MCP > Bridge" in text or (text.startswith("Error") and "pairing" in text.lower()):
        return FAIL, "Revit not paired on that PC: click MCP > Bridge (Disconnected)"
    if "No elements found in category" in text:
        return WARN, "category exists but the model has none of it (empty model?)"
    if text.startswith("Error") or "=== ERROR DETAILS ===" in text:
        first = next((line for line in text.splitlines() if line.startswith("Error")), text)
        return FAIL, first[:200]
    return PASS, ""


def parse_json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


async def call(session, report, name, args=None, timeout=60, expect_image=False, label=None):
    """Call a tool, record the outcome, return (status, text)."""
    args = args or {}
    label = label or name
    started = time.monotonic()
    try:
        with anyio.fail_after(timeout):
            result = await session.call_tool(name, args)
    except TimeoutError:
        report.add(label, FAIL, "timeout after {}s (Revit busy or dialog open?)".format(timeout),
                   args, time.monotonic() - started)
        return FAIL, ""
    except Exception as error:  # protocol/transport errors
        report.add(label, FAIL, "{}: {}".format(type(error).__name__, error), args,
                   time.monotonic() - started)
        return FAIL, ""
    seconds = time.monotonic() - started
    if expect_image:
        images = [item for item in result.content if getattr(item, "type", "") == "image"]
        if images:
            size = len(images[0].data) * 3 // 4
            report.add(label, PASS, "PNG ~{} KB".format(size // 1024), args, seconds)
            return PASS, ""
    text = result_text(result)
    status, detail = (FAIL, "tool reported isError") if result.isError else classify(text)
    report.add(label, status, detail, args, seconds, text[:500])
    return status, text


async def check_remote_auth(url, report):
    """The network server must refuse missing and wrong tokens."""
    headers = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        for label, extra in (("no token", {}),
                             ("wrong token", {"Authorization": "Bearer " + "0" * 64})):
            try:
                response = await client.post(url, json=INIT, headers=dict(headers, **extra))
            except httpx.HTTPError as error:
                report.add("auth: " + label, FAIL, "cannot reach server: {}".format(error))
                return False
            if response.status_code == 401:
                report.add("auth: " + label, PASS, "401 as expected")
            else:
                report.add("auth: " + label, FAIL,
                           "expected 401, got {}".format(response.status_code))
    return True


@asynccontextmanager
async def open_session(args):
    if args.local:
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "main.py")],
                                       cwd=str(ROOT), env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
    else:
        headers = {"Authorization": "Bearer " + args.token}
        timeout = httpx.Timeout(args.timeout, read=args.timeout + 30)
        async with httpx.AsyncClient(headers=headers, timeout=timeout, trust_env=False) as http:
            async with streamable_http_client(args.url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    yield session


async def run(args, report):
    if not args.local and not await check_remote_auth(args.url, report):
        return

    async with open_session(args) as session:
        info = await session.initialize()
        report.add("initialize", PASS, "server {} / protocol {}".format(
            info.serverInfo.name, info.protocolVersion))

        names = {tool.name for tool in (await session.list_tools()).tools}
        expected = set(TOOL_DESCRIPTIONS)
        if names == expected:
            report.add("tool inventory", PASS, "{} tools".format(len(names)))
        else:
            report.add("tool inventory", FAIL, "missing on server: {}; extra on server: {}".format(
                sorted(expected - names) or "-", sorted(names - expected) or "-"))

        status, text = await call(session, report, "get_revit_status", timeout=15)
        if status == FAIL:
            report.add("remaining checks", SKIP, "Revit bridge not reachable; fix status first")
            return

        for name in ("get_revit_model_info", "list_levels", "get_current_view_info",
                     "list_family_categories", "list_revit_installations"):
            await call(session, report, name, timeout=args.timeout)
        await call(session, report, "list_families", {"limit": 20}, timeout=args.timeout)
        await call(session, report, "list_category_parameters", {"category_name": "Walls"},
                   timeout=args.timeout)
        await call(session, report, "model_health_report", {"sample_ids": 3},
                   timeout=max(args.timeout, 120))

        status, text = await call(session, report, "get_current_view_elements",
                                  {"limit": 20}, timeout=args.timeout)
        if status == PASS and parse_json(text) is None:
            report.add("get_current_view_elements data", WARN,
                       "response had only a message (truncated); element data was dropped "
                       "by format_response")

        status, text = await call(session, report, "list_revit_views", timeout=args.timeout)
        views = (parse_json(text) or {}).get("views_by_type", {})
        view_name = next((views[kind][0] for kind in VIEW_TYPE_ORDER if views.get(kind)), None)
        if view_name:
            await call(session, report, "get_revit_view", {"view_name": view_name},
                       timeout=max(args.timeout, 90), expect_image=True)
        else:
            report.add("get_revit_view", SKIP, "no exportable view found")

        status, text = await call(session, report, "get_selected_elements", {"limit": 50},
                                  timeout=args.timeout)
        selection = parse_json(text) or {}
        ids = [e["element_id"] for e in selection.get("elements", []) if "element_id" in e][:3]
        if status == PASS and not ids:
            report.add("selection content", WARN,
                       "nothing selected in Revit; select 1-3 elements to test parameters")
        if ids:
            status, text = await call(session, report, "get_element_parameters",
                                      {"element_ids": ids, "include_type": True},
                                      timeout=args.timeout)
            data = parse_json(text) or {}
            if status == PASS and not data.get("elements"):
                report.add("get_element_parameters content", WARN,
                           "no elements returned for ids {}".format(ids))
            await call(session, report, "get_element_parameters", {}, timeout=args.timeout,
                       label="get_element_parameters (current selection)")
            # Dry run only: Revit validates inside a transaction that is rolled back.
            changes = [{"element_id": i, "parameter": "ALL_MODEL_INSTANCE_COMMENTS",
                        "value": "smoke test"} for i in ids]
            status, text = await call(session, report, "bulk_set_parameters",
                                      {"changes": changes, "dry_run": True},
                                      timeout=args.timeout, label="bulk_set_parameters (dry run)")
            if status == FAIL and "Parameter not found" in text:
                report.checks[-1]["status"] = WARN
                report.checks[-1]["detail"] = "selected element has no 'Comments' parameter"
        else:
            report.add("get_element_parameters", SKIP, "needs a selection")

        skipped = sorted(expected - READ_ONLY - {"bulk_set_parameters"})
        report.add("write tools", SKIP, "not called by the smoke test: " + ", ".join(skipped))


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="Remote server, e.g. http://192.168.0.20:8000/mcp")
    target.add_argument("--local", action="store_true", help="Start main.py over stdio on this PC")
    parser.add_argument("--token", default=os.environ.get("REVIT_MCP_TOKEN"),
                        help="Remote access token (or env REVIT_MCP_TOKEN)")
    parser.add_argument("--timeout", type=float, default=60, help="Seconds per tool call")
    parser.add_argument("--report", default="smoke-report.json", help="JSON report path")
    args = parser.parse_args(argv)
    if args.url and not args.token:
        parser.error("--token (or REVIT_MCP_TOKEN) is required with --url")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = Report()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        anyio.run(run, args, report)
    except Exception as error:
        report.add("session", FAIL, "{}: {}".format(type(error).__name__, error))
    counts = report.summary()
    print("\n{PASS} passed, {WARN} warnings, {SKIP} skipped, {FAIL} failed".format(**counts))
    Path(args.report).write_text(json.dumps({
        "started": started,
        "target": "local stdio" if args.local else args.url,
        "summary": counts,
        "checks": report.checks,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Report: {}".format(Path(args.report).resolve()))
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
