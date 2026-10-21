# -*- coding: utf-8 -*-
import argparse
import functools
import os
import sys
import httpx
import anyio
from mcp.server.fastmcp import FastMCP, Image, Context
import base64
from typing import Optional, Dict, Any, Union
from revit_mcp.pairing import authorization_body, validate_request
from tools.http_security import PairingMiddleware
from tools.remote_access import (
    LOOPBACK_HOSTS,
    RemoteAccessError,
    create_remote_token,
    get_remote_token,
    revoke_remote_token,
    validate_remote_request,
)

# Create a generic MCP server for interacting with Revit
# Use stateless_http=True and json_response=True for better compatibility
mcp = FastMCP(
    "Revit MCP Server", 
    host="127.0.0.1", 
    port=8000,
    stateless_http=True,
    json_response=True
)

# Configuration
# The submitted local architecture only connects to the same machine.
# Remote pyRevit Routes requires a separately reviewed authenticated HTTPS design.
REVIT_HOST = os.environ.get("REVIT_HOST", "127.0.0.1")
if REVIT_HOST not in {"127.0.0.1", "localhost", "::1"}:
    raise ValueError("REVIT_HOST must be loopback; remote HTTP bridges are not supported.")
REVIT_PORT = int(os.environ.get("REVIT_PORT", "48884"))
if not 1 <= REVIT_PORT <= 65535:
    raise ValueError("REVIT_PORT must be between 1 and 65535.")
URL_HOST = "[::1]" if REVIT_HOST == "::1" else REVIT_HOST
BASE_URL = f"http://{URL_HOST}:{REVIT_PORT}/revit_mcp"


async def revit_get(endpoint: str, ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple GET request to Revit API"""
    return await _revit_call("GET", endpoint, ctx=ctx, **kwargs)


async def revit_post(endpoint: str, data: Dict[str, Any], ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple POST request to Revit API"""
    return await _revit_call("POST", endpoint, data=data, ctx=ctx, **kwargs)


async def revit_image(endpoint: str, ctx: Context = None) -> Union[Image, str]:
    """GET request that returns an Image object"""
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.request("GET", f"{BASE_URL}{endpoint}", json=authorization_body())
            
            if response.status_code == 200:
                data = response.json()
                image_bytes = base64.b64decode(data["image_data"])
                return Image(data=image_bytes, format="png")
            else:
                return f"Error: {response.status_code} - {response.text}"
    except httpx.TimeoutException:
        return "Error: Image export timed out after 60 seconds."
    except Exception as e:
        msg = str(e) or type(e).__name__
        return f"Error: {msg}"


async def _revit_call(method: str, endpoint: str, data: Dict = None, ctx: Context = None,
                     timeout: float = 30.0, params: Dict = None) -> Union[Dict, str]:
    """Internal function handling all HTTP calls"""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            url = f"{BASE_URL}{endpoint}"

            # pyRevit Routes hides headers from route handlers, so the pairing
            # secret travels in the JSON body of every call, GET included.
            if method == "GET":
                response = await client.request("GET", url, params=params, json=authorization_body())
            else:  # POST
                response = await client.post(url, json=authorization_body(data))

            return response.json() if response.status_code == 200 else f"Error: {response.status_code} - {response.text}"
    except httpx.TimeoutException:
        return f"Error: Request timed out after {timeout} seconds. The operation may still be running in Revit."
    except Exception as e:
        msg = str(e) or type(e).__name__
        return f"Error: {msg}"


# Register all tools BEFORE the main block
from tools import register_tools
register_tools(mcp, revit_get, revit_post, revit_image)


def build_http_app(transport, remote=False):
    """Build an authenticated app for the selected HTTP transport.

    Loopback mode checks the pairing secret; remote (network) mode checks the
    separate remote access token.
    """
    if transport not in {"sse", "streamable-http", "combined"}:
        raise ValueError("Unknown HTTP transport.")
    if transport == "sse":
        http_app = mcp.sse_app()
    else:
        http_app = mcp.streamable_http_app()
        if transport == "combined":
            http_app.routes.extend(mcp.sse_app().routes)
    validator = validate_remote_request if remote else validate_request
    http_app.add_middleware(PairingMiddleware, validator=validator)
    return http_app


def network_host_names(bind_host, extra_hosts=()):
    """Host header values a remote client may use to reach this server."""
    import socket
    names = set(extra_hosts)
    if bind_host in {"0.0.0.0", "::"}:
        hostname = socket.gethostname()
        names.update({hostname, socket.getfqdn()})
        try:
            names.update(socket.gethostbyname_ex(hostname)[2])
        except OSError:
            pass
        try:
            # Address of the interface holding the default route; no packet is sent.
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("10.255.255.255", 1))
                names.add(probe.getsockname()[0])
            finally:
                probe.close()
        except OSError:
            pass
    else:
        names.add(bind_host)
    return sorted(name for name in names
                  if name and name not in LOOPBACK_HOSTS
                  and not name.endswith(".arpa") and name != "0.0.0.0")


def allow_network_hosts(names):
    """FastMCP's DNS-rebinding protection only admits loopback Host headers
    by default; add the names remote clients will use."""
    security = mcp.settings.transport_security
    if security is None:
        return
    for name in names:
        entry = "[{}]:*".format(name) if ":" in name else "{}:*".format(name)
        if entry not in security.allowed_hosts:
            security.allowed_hosts.append(entry)


async def run_http_async(transport, host=None, port=None, remote=False,
                         ssl_certfile=None, ssl_keyfile=None):
    """Run authenticated SSE, streamable HTTP, or both."""
    import uvicorn
    http_app = build_http_app(transport, remote=remote)

    config = uvicorn.Config(
        http_app,
        host=host or mcp.settings.host,
        port=port or mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
    server = uvicorn.Server(config)
    await server.serve()


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Revit MCP Server")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--sse", dest="transport", action="store_const", const="sse")
    modes.add_argument("--http", "--streamable-http", dest="transport",
                       action="store_const", const="streamable-http")
    modes.add_argument("--combined", dest="transport", action="store_const", const="combined")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Address to listen on. A non-loopback address (e.g. 0.0.0.0 "
                             "or this PC's IP) enables network access with the remote token.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="Extra host name clients use to reach this PC (repeatable).")
    parser.add_argument("--ssl-certfile", help="TLS certificate (PEM) for network mode.")
    parser.add_argument("--ssl-keyfile", help="TLS private key (PEM) for network mode.")
    parser.add_argument("--remote-token", choices=["new", "revoke"],
                        help="Create/rotate or revoke the network access token, then exit.")
    parser.set_defaults(transport="stdio")
    args = parser.parse_args(argv)

    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("--ssl-certfile and --ssl-keyfile must be used together.")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")
    args.remote = args.host not in LOOPBACK_HOSTS
    if args.remote and args.transport == "stdio":
        parser.error("Network access needs an HTTP transport: add --http.")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.remote_token == "new":
        token = create_remote_token()
        print("Remote access token (shown once; rotating invalidates the previous one):")
        print(token)
        return
    if args.remote_token == "revoke":
        revoke_remote_token()
        print("Remote access token revoked.")
        return

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    if args.remote:
        try:
            get_remote_token()
        except RemoteAccessError as error:
            sys.exit(str(error))
        names = network_host_names(args.host, args.allowed_host)
        allow_network_hosts(names)
        scheme = "https" if args.ssl_certfile else "http"
        print("Network mode. Clients connect to:", file=sys.stderr)
        for name in names:
            print("  {}://{}:{}/mcp".format(scheme, name, args.port), file=sys.stderr)
        if not args.ssl_certfile:
            print("WARNING: no TLS. The token travels in clear text on the network; "
                  "use --ssl-certfile/--ssl-keyfile outside a trusted LAN.", file=sys.stderr)

    anyio.run(functools.partial(
        run_http_async, args.transport, host=args.host, port=args.port,
        remote=args.remote, ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile))


if __name__ == "__main__":
    main()