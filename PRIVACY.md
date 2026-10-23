# Privacy and data access

This information describes the Autodesk review candidate. It does not claim
Autodesk certification or a security certification.

The MCP server and pyRevit bridge run on your Windows computer. The built-in
client connects to the selected loopback pyRevit Routes port. It does not call
an AI service directly or operate a publisher-hosted model database. Your chosen
MCP client receives tool responses and may forward them to its AI provider.
That client's provider, consent settings, storage and deletion policies apply
separately. Use only data you are authorized to share with that client/provider.

## Information handled

Tools can return project/client names, project numbers, model titles and paths,
room/level/view/sheet details, model/link metadata, element IDs, family types,
parameter names and sample values, geometry locations, warnings and view images.
Revit discovery reads Revit-specific installation registry keys and executable
locations. Launch/document tools handle the file paths and options you supply.
Modification tools change models or view overrides and document lifecycle tools
can write files or synchronize a workshared model with its central location.

`execute_revit_code` is not a sandbox. Supplied code runs with your Windows user's
permissions and can read/write files, access unrelated data or contact network
services. Its behavior cannot be reduced to an enumerated list of destinations
or data fields. A separate prompt in Revit displays the request before execution.
Do not approve code you do not understand. Autodesk eligibility for this capability
is still unresolved; this limitation is recorded in the declaration draft.

The connector has no account/profile, age or date-of-birth collection flow.
Arbitrary supplied code or model fields may nonetheless contain personal data.
It is intended for adult professional/design workflows, not children.

## Consent and revocation

In this fork the extension pairs the Windows user automatically the first time it
loads (decision of 2026-10-22); the Connect ribbon button explains model access,
client/AI disclosure and Python execution when pairing again after a Disconnect.
Pairing persists across Revit restarts
and is shared by instances running as the same Windows user. Model modifications
and Python execution require separate request review in Revit. Local discovery
and launch are permitted by pairing; client confirmation settings still apply.
Disconnect removes the secret, denies subsequent requests and stops the automatic
pairing until the user connects again. A request already
running may finish; Disconnect does not undo changes or recall client responses.

## Storage, retention and deletion

Tool responses are normally held in memory while processing a request. The
connector does not maintain a separate persistent response database. Windows,
Revit/pyRevit logs and the chosen MCP client may retain diagnostics, model names,
paths, request descriptions, error messages or transcripts. Their retention is
controlled by their configuration; this connector imposes no timed deletion.

Pairing is stored encrypted using Windows DPAPI CurrentUser in
`%LOCALAPPDATA%\RevitMCPPython\pairing.dpapi` until Disconnect or manual removal.
Connect rotates this secret. Processes with the same Windows identity can decrypt
it; administrators and a compromised user session remain outside its protection.

View exports use a fresh temporary directory per request and attempt cleanup on
success and failure. If the process terminates or deletion fails, remove residual
`RevitMCPExports-*` directories from your Windows temporary directory. Model files
you save and arbitrary files created by supplied code remain until you delete them.
Deleting this connector does not delete saved models or files in your MCP client.

## Questions and requests

Contact the publisher through the repository's GitHub issues for general questions.
Never post model data, secrets or personal information in a public issue. There is
no publisher-hosted model data store to request deletion from; delete local files
and use your AI client's deletion controls for any responses already transmitted.
