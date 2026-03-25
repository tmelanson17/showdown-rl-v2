"""
ipc.py — Lightweight Unix Domain Socket IPC layer.

Provides a simple request/response API over UDS with length-prefixed JSON
framing.  The GameRunner (or any orchestrator) acts as the server; external
agents (e.g. ML model servers) connect as clients.

Wire format (per message):
    [4 bytes, big-endian uint32: payload length][payload bytes (UTF-8 JSON)]

Usage:
    # Server side (inside GameRunner or a dedicated dispatcher)
    server = IPCServer("/tmp/ps_agent.sock")
    server.register_handler("decide", my_decide_handler)
    server.serve_forever()          # blocking; run in a thread if needed

    # Client side (inside a ModelAgent subprocess or remote process)
    client = IPCClient("/tmp/ps_agent.sock")
    result = client.call("decide", {"gamestate": {...}})
"""

import json
import socket
import struct
import threading
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Framing helpers
# ---------------------------------------------------------------------------
_HEADER_FMT = ">I"          # 4-byte big-endian unsigned int
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


def _send_msg(sock: socket.socket, payload: dict) -> None:
    """Serialize *payload* to JSON and send with a length prefix."""
    data = json.dumps(payload).encode("utf-8")
    header = struct.pack(_HEADER_FMT, len(data))
    sock.sendall(header + data)


def _recv_msg(sock: socket.socket) -> Optional[dict]:
    """Read one length-prefixed JSON message.  Returns None on EOF."""
    raw_header = _recv_exact(sock, _HEADER_SIZE)
    if raw_header is None:
        return None
    (length,) = struct.unpack(_HEADER_FMT, raw_header)
    raw_body = _recv_exact(sock, length)
    if raw_body is None:
        return None
    return json.loads(raw_body.decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly *n* bytes, or return None on clean close."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None if buf == b"" else None   # treat partial as EOF
        buf += chunk
    return buf


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
class IPCServer:
    """A Unix domain socket server that dispatches JSON-RPC-style calls.

    Handlers are registered by method name.  Each handler receives the
    ``params`` dict from the incoming message and must return a JSON-
    serializable result (or raise an exception, which is caught and
    returned as an error response).
    """

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._handlers: Dict[str, Callable[..., Any]] = {}
        self._server_sock: Optional[socket.socket] = None
        self._shutdown_event = threading.Event()

    # -- registration -------------------------------------------------------
    def register_handler(self, method: str, handler: Callable[..., Any]) -> None:
        """Register *handler* for *method*.  Handler signature: (params: dict) -> Any."""
        self._handlers[method] = handler

    # -- lifecycle ----------------------------------------------------------
    def serve_forever(self) -> None:
        """Block and accept connections until :py:meth:`shutdown` is called."""
        import os, atexit

        # Clean up stale socket file if present
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self.socket_path)
        self._server_sock.listen(5)
        self._server_sock.settimeout(1.0)   # so we can check _shutdown_event

        atexit.register(self._cleanup)
        logger.info("IPCServer listening on %s", self.socket_path)

        while not self._shutdown_event.is_set():
            try:
                conn, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            t.start()

    def shutdown(self) -> None:
        self._shutdown_event.set()
        if self._server_sock:
            self._server_sock.close()
        self._cleanup()

    def _cleanup(self) -> None:
        import os
        if self._server_sock:
            self._server_sock.close()
            self._server_sock = None
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    # -- per-client loop ----------------------------------------------------
    def _handle_client(self, conn: socket.socket) -> None:
        try:
            while True:
                msg = _recv_msg(conn)
                if msg is None:
                    break                       # client disconnected
                response = self._dispatch(msg)
                _send_msg(conn, response)
        except Exception:
            logger.exception("Error handling client")
        finally:
            conn.close()

    def _dispatch(self, msg: dict) -> dict:
        method = msg.get("method")
        params = msg.get("params", {})
        msg_id = msg.get("id")

        handler = self._handlers.get(method)
        if handler is None:
            return {"id": msg_id, "error": f"Unknown method: {method}"}

        try:
            result = handler(params)
            return {"id": msg_id, "result": result}
        except Exception as exc:
            logger.exception("Handler %s raised", method)
            return {"id": msg_id, "error": str(exc)}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class IPCClient:
    """A Unix domain socket client with a blocking ``call`` API.

    Maintains a persistent connection to the server for the lifetime of the
    client (reconnects on the next call if the connection drops).
    """

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._sock: Optional[socket.socket] = None
        self._call_id = 0
        self._lock = threading.Lock()          # one call at a time per client

    # -- connection ---------------------------------------------------------
    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self.socket_path)
        logger.info("IPCClient connected to %s", self.socket_path)

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def _ensure_connected(self) -> None:
        if self._sock is None:
            self.connect()

    # -- RPC ----------------------------------------------------------------
    def call(self, method: str, params: Optional[dict] = None) -> Any:
        """Send a synchronous request and return the result.

        Raises:
            RuntimeError: if the server returns an error.
            ConnectionError: on socket failures.
        """
        with self._lock:
            self._ensure_connected()
            self._call_id += 1
            msg = {"id": self._call_id, "method": method, "params": params or {}}
            try:
                _send_msg(self._sock, msg)
                response = _recv_msg(self._sock)
            except Exception:
                self.close()
                raise ConnectionError("Lost connection to IPC server")

            if response is None:
                self.close()
                raise ConnectionError("Server closed connection unexpectedly")

            if "error" in response:
                raise RuntimeError(f"IPC error from server: {response['error']}")

            return response.get("result")
