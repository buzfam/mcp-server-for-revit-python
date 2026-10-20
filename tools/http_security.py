"""Authenticate optional HTTP transports with a bearer secret."""
from revit_mcp.pairing import validate_request


class PairingMiddleware:
    """Loopback mode validates the pairing secret; network mode passes
    tools.remote_access.validate_remote_request as the validator."""

    def __init__(self, app, validator=validate_request):
        self.app = app
        self.validator = validator

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            headers = {key.decode("latin-1"): value.decode("latin-1")
                       for key, value in scope.get("headers", [])}
            if not self.validator(headers):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"text/plain"),
                                            (b"www-authenticate", b"Bearer")]})
                    await send({"type": "http.response.body", "body": b"MCP pairing required."})
                return
        await self.app(scope, receive, send)
