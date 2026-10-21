# -*- coding: utf-8 -*-
"""Start/stop the network MCP server (main.py) from the Revit ribbon.

Used by MCP.tab/Remote.panel buttons (IronPython). Pure helpers at the top are
also unit tested under CPython; .NET imports stay inside the functions that
need them.
"""
import json
import os
import time

DEFAULT_PORT = 8000
UV_INSTALL_COMMAND = (
    'powershell -NoProfile -ExecutionPolicy ByPass -c '
    '"irm https://astral.sh/uv/install.ps1 | iex"'
)


def state_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "RevitMCPPython")


def state_path():
    return os.path.join(state_dir(), "remote-server.json")


def log_path():
    return os.path.join(state_dir(), "remote-server.log")


def extension_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_uv(environ=None, isfile=os.path.isfile):
    """Locate uv.exe on PATH or in the installer's default folders."""
    environ = os.environ if environ is None else environ
    candidates = []
    for folder in environ.get("PATH", "").split(os.pathsep):
        if folder:
            candidates.append(os.path.join(folder.strip('"'), "uv.exe"))
    home = environ.get("USERPROFILE")
    if home:
        candidates.append(os.path.join(home, ".local", "bin", "uv.exe"))
        candidates.append(os.path.join(home, ".cargo", "bin", "uv.exe"))
    local = environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Programs", "uv", "uv.exe"))
    for candidate in candidates:
        if isfile(candidate):
            return candidate
    return None


def server_arguments(port):
    return "run --frozen main.py --http --host 0.0.0.0 --port {}".format(int(port))


def cmd_arguments(uv_path, port, log_file):
    """cmd.exe /c line that runs the server and appends output to the log."""
    return '/c ""{}" {} >> "{}" 2>&1"'.format(uv_path, server_arguments(port), log_file)


def connection_command(name, ip, port, token):
    return ('claude mcp add --transport http {} http://{}:{}/mcp '
            '--header "Authorization: Bearer {}"').format(name, ip, int(port), token)


def client_name(machine):
    return "revit-" + "".join(c for c in machine.lower() if c.isalnum() or c == "-")


def read_state():
    try:
        with open(state_path()) as stream:
            return json.load(stream)
    except (IOError, OSError, ValueError):
        return None


def write_state(state):
    directory = state_dir()
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(state_path(), "w") as stream:
        json.dump(state, stream)


def clear_state():
    if os.path.isfile(state_path()):
        os.remove(state_path())


def log_shows_ready(text):
    return "Uvicorn running" in text


def log_tail(lines=15):
    try:
        with open(log_path()) as stream:
            return "".join(stream.readlines()[-lines:])
    except (IOError, OSError):
        return ""


# ---- .NET-only below (IronPython inside Revit) ----

def local_ipv4_addresses():
    from System.Net import Dns, IPAddress
    from System.Net.Sockets import AddressFamily
    result = []
    for address in Dns.GetHostAddresses(Dns.GetHostName()):
        if address.AddressFamily == AddressFamily.InterNetwork \
                and not IPAddress.IsLoopback(address):
            text = address.ToString()
            if not text.startswith("169.254.") and text not in result:
                result.append(text)
    return result


def machine_name():
    from System import Environment
    return Environment.MachineName


def is_running():
    state = read_state()
    if not state:
        return False
    from System.Diagnostics import Process
    try:
        process = Process.GetProcessById(int(state["pid"]))
        return not process.HasExited
    except Exception:
        return False


def _hidden_process(file_name, arguments, working_directory=None):
    from System.Diagnostics import Process, ProcessStartInfo
    info = ProcessStartInfo(file_name, arguments)
    info.UseShellExecute = False
    info.CreateNoWindow = True
    if working_directory:
        info.WorkingDirectory = working_directory
    return Process.Start(info)


def install_uv(timeout_sec=300):
    """Run Astral's official installer. Caller must ask for consent first."""
    process = _hidden_process("cmd.exe", "/c " + UV_INSTALL_COMMAND)
    process.WaitForExit(timeout_sec * 1000)
    return find_uv()


def start(port=DEFAULT_PORT, uv_path=None, wait_sec=90):
    """Start main.py in network mode. Returns (ready, message)."""
    if is_running():
        return True, "already running"
    uv_path = uv_path or find_uv()
    if not uv_path:
        return False, "uv not found"
    directory = state_dir()
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(log_path(), "a") as stream:
        stream.write("\n--- start {} ---\n".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    process = _hidden_process(
        "cmd.exe", cmd_arguments(uv_path, port, log_path()), extension_root())
    from .self_update import installed_commit
    write_state({"pid": process.Id, "port": int(port), "commit": installed_commit(extension_root()),
                 "started": time.strftime("%Y-%m-%d %H:%M:%S")})
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if process.HasExited:
            clear_state()
            return False, log_tail()
        if log_shows_ready(log_tail(40)):
            return True, "ready"
        time.sleep(1)
    # First start downloads dependencies; it may still finish later.
    return False, "still starting"


def stop():
    state = read_state()
    if not state:
        return
    # /T kills the whole tree: cmd.exe -> uv -> python.
    _hidden_process("taskkill", "/PID {} /T /F".format(int(state["pid"]))).WaitForExit(10000)
    clear_state()
