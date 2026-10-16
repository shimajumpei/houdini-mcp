"""Open / close / reopen Houdini without restarting the MCP server.

These tests run a real RPyC classic server in a subprocess, the same kind of
server Houdini's hrpyc starts, and give it a stub ``hou`` module. No Houdini
and no license are needed. Stopping and restarting the subprocess on the
same port reproduces "close Houdini, then open it again" while the MCP
server keeps its cached connection.
"""

import os
import socket
import subprocess
import sys
import textwrap
import time

import pytest

import houdini_mcp.connection as conn_module
from houdini_mcp.connection import (
    HoudiniConnectionError,
    ensure_connected,
    get_connection_info,
)

STUB_HOU = textwrap.dedent(
    """
    def applicationVersionString():
        return "stub-99.0.1"

    def applicationVersion():
        return (99, 0, 1)

    class _Node:
        def path(self):
            return "/obj"

    def node(path):
        return _Node() if path == "/obj" else None

    class hipFile:
        @staticmethod
        def path():
            return "untitled.hip"
    """
)

SERVER = textwrap.dedent(
    """
    import sys
    from rpyc.core.service import SlaveService
    from rpyc.utils.server import ThreadedServer

    ThreadedServer(SlaveService, hostname="localhost", port=int(sys.argv[1])).start()
    """
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _wait_listening(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("localhost", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"stub RPyC server did not start on port {port}")


class _FakeHoudini:
    """Start and stop a stub RPyC classic server, like opening and closing Houdini."""

    def __init__(self, tmp_path, port: int):
        (tmp_path / "hou.py").write_text(STUB_HOU, encoding="utf-8")
        self._script = tmp_path / "stub_server.py"
        self._script.write_text(SERVER, encoding="utf-8")
        self._env = dict(os.environ, PYTHONPATH=str(tmp_path))
        self.port = port
        self._proc = None

    def open(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, str(self._script), str(self.port)],
            env=self._env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_listening(self.port)

    def close(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait(timeout=10)
            self._proc = None


@pytest.fixture
def fake_houdini(tmp_path, reset_connection_state, monkeypatch):
    # Skip the backoff sleeps between reconnect attempts so "Houdini closed" fails fast.
    monkeypatch.setattr(conn_module.time, "sleep", lambda _s: None)
    fake = _FakeHoudini(tmp_path, _free_port())
    try:
        yield fake
    finally:
        fake.close()
        conn_module.disconnect()


def test_open_close_reopen_without_mcp_restart(fake_houdini):
    """open → tool works; close → clear error; reopen → next call works."""
    port = fake_houdini.port

    fake_houdini.open()
    hou = ensure_connected("localhost", port)
    assert hou.applicationVersionString() == "stub-99.0.1"

    fake_houdini.close()
    with pytest.raises(HoudiniConnectionError) as exc_info:
        ensure_connected("localhost", port)
    message = str(exc_info.value)
    assert "Failed to connect" in message
    assert "not running" in message
    assert get_connection_info("localhost", port)["connected"] is False

    fake_houdini.open()
    hou = ensure_connected("localhost", port)
    assert hou.applicationVersionString() == "stub-99.0.1"
    assert get_connection_info("localhost", port)["connected"] is True


def test_restart_between_calls_reconnects_transparently(fake_houdini):
    """Houdini restarted between two tool calls: the cached stream is dead, the
    next call must still succeed on its first try (no failed call first)."""
    port = fake_houdini.port

    fake_houdini.open()
    first = ensure_connected("localhost", port)
    assert first.applicationVersionString() == "stub-99.0.1"
    stale = conn_module.get_connection()

    fake_houdini.close()
    fake_houdini.open()

    hou = ensure_connected("localhost", port)
    assert hou.applicationVersionString() == "stub-99.0.1"
    assert conn_module.get_connection() is not stale


def test_check_connection_reports_dead_stream(fake_houdini):
    """A cached connection whose server went away is reported as disconnected."""
    port = fake_houdini.port

    fake_houdini.open()
    ensure_connected("localhost", port)
    fake_houdini.close()

    info = get_connection_info("localhost", port)
    assert info["connected"] is False
    assert info["houdini_version"] is None
