"""One-shot environment diagnosis for the Houdini MCP system.

Checks the full chain in dependency order:

    Python env -> rpyc version -> houdini_mcp install -> Houdini process
    -> RPC port -> RPC roundtrip -> Houdini package install

Each failing check includes a concrete fix instruction.

Usage:
    python -m houdini_mcp.doctor          # human-readable report, exit 1 on failure
    (also exposed as the `doctor` MCP tool)
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_HOST = os.getenv("HOUDINI_HOST", "localhost")
DEFAULT_PORT = int(os.getenv("HOUDINI_PORT", "18811"))

_STATUS_ORDER = {"ok": 0, "warn": 1, "fail": 2}


def _check(
    name: str, status: str, detail: str, fix: Optional[str] = None
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"check": name, "status": status, "detail": detail}
    if fix:
        result["fix"] = fix
    return result


def _check_python() -> Dict[str, Any]:
    return _check(
        "python",
        "ok",
        f"Python {sys.version.split()[0]} @ {sys.executable}",
    )


def _check_rpyc() -> Dict[str, Any]:
    try:
        import rpyc
    except ImportError:
        return _check(
            "rpyc",
            "fail",
            "rpyc is not installed",
            "pip install 'rpyc>=5,<6'",
        )

    ver = getattr(rpyc, "__version__", None)
    if isinstance(ver, tuple):
        ver_str = ".".join(str(v) for v in ver)
    else:
        ver_str = str(ver)

    try:
        major = int(ver_str.split(".")[0])
    except (ValueError, IndexError):
        return _check("rpyc", "warn", f"rpyc version unparseable: {ver_str}")

    if major >= 6:
        return _check(
            "rpyc",
            "fail",
            f"rpyc {ver_str} installed -- 6.x protocol is incompatible with "
            "Houdini's bundled hrpyc",
            "pip install 'rpyc>=5,<6'",
        )
    if major < 5:
        return _check(
            "rpyc",
            "warn",
            f"rpyc {ver_str} installed -- 5.x is the tested series",
            "pip install 'rpyc>=5,<6'",
        )
    return _check("rpyc", "ok", f"rpyc {ver_str}")


def _check_package_install() -> Dict[str, Any]:
    try:
        import houdini_mcp

        location = Path(houdini_mcp.__file__).parent
        return _check("houdini_mcp", "ok", f"houdini_mcp importable @ {location}")
    except ImportError as e:
        return _check(
            "houdini_mcp",
            "fail",
            f"houdini_mcp not importable: {e}",
            "pip install -e <repo root> (the directory containing pyproject/setup)",
        )


def _check_houdini_process() -> Dict[str, Any]:
    if sys.platform != "win32":
        return _check("houdini_process", "warn", "process check only implemented on Windows")

    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        return _check("houdini_process", "warn", f"could not run tasklist: {e}")

    names = set()
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        exe = line.split('","')[0].strip('"').lower()
        if "houdini" in exe or exe in ("hindie.exe", "happrentice.exe"):
            names.add(exe)

    if names:
        return _check("houdini_process", "ok", f"running: {', '.join(sorted(names))}")
    return _check(
        "houdini_process",
        "fail",
        "no Houdini process found",
        "Start Houdini. With the houdini_mcp package installed, the RPyC "
        "listener auto-starts (HOUDINI_MCP_AUTOSTART=0 disables it).",
    )


def _check_port(host: str, port: int) -> Dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            pass
        return _check("rpc_port", "ok", f"{host}:{port} is accepting connections")
    except Exception as e:
        return _check(
            "rpc_port",
            "fail",
            f"{host}:{port} is not reachable ({type(e).__name__})",
            "In Houdini: Python Shell -> "
            "'from houdini_mcp_plugin import start_hrpyc_server; start_hrpyc_server()' "
            "or restart Houdini with the houdini_mcp package installed.",
        )


def _check_roundtrip(host: str, port: int) -> Dict[str, Any]:
    try:
        import rpyc

        conn = rpyc.classic.connect(host, port)
        try:
            if hasattr(conn, "_config"):
                conn._config["sync_request_timeout"] = 10.0
            version = conn.modules.hou.applicationVersionString()
            hip = conn.modules.hou.hipFile.basename()
        finally:
            conn.close()
        return _check(
            "rpc_roundtrip", "ok", f"Houdini {version} responding (hip: {hip})"
        )
    except Exception as e:
        return _check(
            "rpc_roundtrip",
            "fail",
            f"RPC call failed: {type(e).__name__}: {e}",
            "Port is open but hou is not responding. Check that the listener "
            "was started inside Houdini (not another app on this port), and "
            "that client rpyc is 5.x.",
        )


def _check_houdini_packages() -> Dict[str, Any]:
    """Check that the auto-start package file is installed in Houdini prefs."""
    candidates: List[Path] = []

    pref_env = os.environ.get("HOUDINI_USER_PREF_DIR")
    if pref_env:
        candidates.append(Path(pref_env))

    docs = Path.home() / "Documents"
    if docs.exists():
        candidates.extend(sorted(docs.glob("houdini*")))

    installed = [
        p / "packages" / "houdini_mcp.json"
        for p in candidates
        if (p / "packages" / "houdini_mcp.json").exists()
    ]

    if installed:
        return _check(
            "houdini_package",
            "ok",
            "package file installed: " + "; ".join(str(p) for p in installed),
        )
    if not candidates:
        return _check(
            "houdini_package", "warn", "no Houdini preference directory found"
        )
    return _check(
        "houdini_package",
        "warn",
        "houdini_mcp.json not found in any Houdini packages directory -- "
        "the RPyC listener will not auto-start",
        "Copy the package JSON into "
        "Documents/houdini<ver>/packages/houdini_mcp.json pointing "
        "HOUDINI_MCP_ROOT at <repo>/houdini_plugin.",
    )


def run_doctor(host: Optional[str] = None, port: Optional[int] = None) -> Dict[str, Any]:
    """Run all diagnosis checks and return a structured report."""
    host = host or DEFAULT_HOST
    port = port or DEFAULT_PORT

    checks: List[Dict[str, Any]] = [
        _check_python(),
        _check_rpyc(),
        _check_package_install(),
        _check_houdini_process(),
        _check_port(host, port),
    ]

    # Roundtrip only makes sense if the port is open
    port_ok = checks[-1]["status"] == "ok"
    if port_ok:
        checks.append(_check_roundtrip(host, port))
    else:
        checks.append(
            _check("rpc_roundtrip", "fail", "skipped: port not reachable")
        )

    checks.append(_check_houdini_packages())

    worst = max(_STATUS_ORDER[c["status"]] for c in checks)
    overall = [k for k, v in _STATUS_ORDER.items() if v == worst][0]

    failing = [c for c in checks if c["status"] == "fail"]
    if failing:
        # First failure in chain order is the root cause to fix first
        first = failing[0]
        summary = (
            f"{len(failing)} check(s) failing. Fix first: [{first['check']}] "
            f"{first.get('fix', first['detail'])}"
        )
    elif overall == "warn":
        summary = "Working, with warnings. See individual checks."
    else:
        summary = f"All checks passed. Houdini MCP is fully operational on {host}:{port}."

    return {
        "status": overall,
        "host": host,
        "port": port,
        "checks": checks,
        "summary": summary,
    }


def main() -> None:
    report = run_doctor()

    icons = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}
    print(f"Houdini MCP doctor -- target {report['host']}:{report['port']}\n")
    for c in report["checks"]:
        print(f"  {icons[c['status']]} {c['check']}: {c['detail']}")
        if c.get("fix"):
            print(f"         fix: {c['fix']}")
    print(f"\n{report['summary']}")

    if os.environ.get("DOCTOR_JSON"):
        print(json.dumps(report, indent=2, ensure_ascii=False))

    sys.exit(1 if report["status"] == "fail" else 0)


if __name__ == "__main__":
    main()
