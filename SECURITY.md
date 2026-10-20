> **Fork buzfam:** este fork acrescenta um modo remoto opcional (`main.py --http --host`)
> com token próprio, lista de `Host` e TLS opcional; a ponte com o Revit continua só em
> loopback. O texto abaixo é do upstream e descreve o modo local. Veja
> [docs/specs/006-network-access.md](docs/specs/006-network-access.md).

# Security boundaries and reporting

The review candidate requires Windows-user pairing for every registered tool.
The MCP client and all Revit bridge routes authenticate requests with a random
256-bit bearer secret protected at rest by Windows DPAPI CurrentUser. Tokens are
sent in headers, never URL parameters. The built-in client rejects non-loopback
hosts and ignores environment HTTP proxies. Optional SSE/streamable HTTP modes
bind to loopback and require the same bearer header. Browser Origin headers are
rejected. The publication candidate uses `stdio`.

The Revit-side request review runs in pyRevit's Revit API context. Every bridge
route requests a `request` argument and checks authorization before model access;
Revit-accessing routes request `doc`/`uidoc` so pyRevit schedules an ExternalEvent.
Requests using pyRevit's callbackUrl feature are rejected and the callback is
removed before dispatch returns, preventing callback exfiltration by these routes.
This does not harden other pyRevit extensions or pyRevit's own routes.

Pairing is shared by the same Windows user across Revit instances. It protects
against an unpaired remote caller/browser, not other programs running as that
user. Check pyRevit Routes binding and Windows firewall configuration; do not
expose Routes or the HTTP MCP listener to a LAN, tunnel or the internet. Remote
VM/LAN HTTP bridge configurations are deliberately rejected in this candidate.

Python execution is NOT sandboxed. Revit's review prompt is a human review gate,
not a restriction on imports, filesystem access, network calls, Revit APIs or
Windows-user permissions. Other tools can write models, export images or synchronize
with central. Preserve backups and review actual requests. An HTTP timeout does not
prove that a model operation stopped; do not automatically retry a write.

No SOC2, ISO27001 or Autodesk security certification is claimed. The publication
checklist lists runtime, installer, vulnerability scan and Autodesk decisions still
required. Automated checks do not prove Revit 2027 behavior or certification.

Report vulnerabilities privately through GitHub's security advisory feature where
available, or contact a repository maintainer privately. Do not include credentials
or private models in public issues.
