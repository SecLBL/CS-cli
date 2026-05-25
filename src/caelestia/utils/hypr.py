import json as _json
import os
import socket
from typing import Any

socket_base = f"{os.getenv('XDG_RUNTIME_DIR')}/hypr/{os.getenv('HYPRLAND_INSTANCE_SIGNATURE')}"
socket_path = f"{socket_base}/.socket.sock"
socket2_path = f"{socket_base}/.socket2.sock"


def message(msg: str, is_json: bool = True) -> str | dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_path)

        if is_json:
            msg = f"j/{msg}"
        sock.send(msg.encode())

        resp = sock.recv(8192).decode()
        while True:
            new_resp = sock.recv(8192)
            if not new_resp:
                break
            resp += new_resp.decode()

        return _json.loads(resp) if is_json else resp


def dispatch(dispatcher: str, *args: str) -> bool:
    # Hyprland 0.55 Lua config mode: socket 'dispatch X Y' is evaluated as
    # 'return hl.dispatch(X Y)' — invalid Lua for multi-word args. Use eval
    # with native hl.dsp.* expressions mapped from the old string dispatchers.
    arg = " ".join(map(str, args)).strip()
    lua = _to_lua(dispatcher, arg)
    return message(f"eval {lua}", is_json=False) == "ok"


def batch(*msgs: str, is_json: bool = False) -> str | dict[str, Any]:
    # resizer.py passes raw "dispatch X Y" strings — parse and eval each one.
    results = []
    for msg in msgs:
        if msg.startswith("dispatch "):
            rest = msg[len("dispatch "):]
            parts = rest.split(" ", 1)
            dispatcher = parts[0]
            arg = parts[1] if len(parts) > 1 else ""
            result = message(f"eval {_to_lua(dispatcher, arg)}", is_json=False)
        elif is_json:
            result = message(f"j/{msg.strip()}", is_json=False)
        else:
            result = message(msg, is_json=False)
        results.append(result)
    return "\n".join(results)


# ── Lua dispatch mapping ───────────────────────────────────────────────────────

def _s(s: str) -> str:
    """Escape a string as a Lua string literal (JSON strings are valid Lua)."""
    return _json.dumps(s)


def _parse_addr(arg: str) -> tuple[str, str | None]:
    """Split 'workspace,address:0x...' or 'exact W H,address:0x...' args."""
    parts = arg.split(",")
    primary = parts[0]
    address = next((p[8:] for p in parts[1:] if p.startswith("address:")), None)
    return primary, address


def _to_lua(dispatcher: str, arg: str) -> str:
    """Map a string dispatcher + arg to a Lua hl.dispatch() expression."""

    if dispatcher == "togglespecialworkspace":
        name = arg or "special"
        return f"hl.dispatch(hl.dsp.workspace.toggle_special({_s(name)}))"

    if dispatcher in ("movetoworkspace", "movetoworkspacesilent"):
        # arg: "special:music,address:0x123" or "special:music"
        workspace, address = _parse_addr(arg)
        tbl = f"workspace={_s(workspace)}"
        if address:
            tbl += f", address={_s(address)}"
        return f"hl.dispatch(hl.dsp.window.move({{{tbl}}}))"

    if dispatcher == "exec":
        return f"hl.dispatch(hl.dsp.exec_cmd({_s(arg)}))"

    if dispatcher in ("resizewindowpixel", "resizeactive"):
        # arg: "exact W H,address:0x..." or "W H,address:0x..."
        primary, address = _parse_addr(arg)
        exact = primary.startswith("exact ")
        coords = primary.removeprefix("exact ").split()
        w, h = (int(coords[0]), int(coords[1])) if len(coords) >= 2 else (0, 0)
        tbl = f"x={w}, y={h}"
        if exact:
            tbl += ", exact=true"
        if address:
            tbl += f", address={_s(address)}"
        return f"hl.dispatch(hl.dsp.window.resize({{{tbl}}}))"

    if dispatcher in ("movewindowpixel", "moveactive"):
        # arg: "exact X Y,address:0x..." or "X Y,address:0x..."
        primary, address = _parse_addr(arg)
        primary = primary.removeprefix("exact ")
        coords = primary.split()
        x, y = (int(coords[0]), int(coords[1])) if len(coords) >= 2 else (0, 0)
        tbl = f"x={x}, y={y}"
        if address:
            tbl += f", address={_s(address)}"
        return f"hl.dispatch(hl.dsp.window.move({{{tbl}}}))"

    if dispatcher == "togglefloating":
        tbl = 'action="toggle"'
        if arg.startswith("address:"):
            tbl += f", address={_s(arg[8:])}"
        return f"hl.dispatch(hl.dsp.window.float({{{tbl}}}))"

    if dispatcher == "centerwindow":
        return "hl.dispatch(hl.dsp.window.center())"

    # Unknown dispatcher — surface the error through Hyprland's error channel
    return f"error({_s('caelestia: unsupported dispatcher in Lua mode: ' + dispatcher)})"
