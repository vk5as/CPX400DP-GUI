"""Raw TCP transport for the CPX400DP LAN interface (port 9221).

Framing per the manual: commands are sent LF-terminated, responses come back
CR LF terminated. This module owns the socket and byte framing only; it does
not know about specific commands (see protocol.py) and does not touch Tk.
"""

from __future__ import annotations

import socket
import time


class TransportError(IOError):
    """Raised for connection failures, timeouts, and framing errors."""


DEFAULT_PORT = 9221
DEFAULT_TIMEOUT_S = 2.0
RESPONSE_TERMINATOR = b"\r\n"


class TcpTransport:
    """A single TCP connection to the instrument.

    Not thread-safe by design: exactly one thread (the worker) is expected
    to own an instance at a time.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None
        self._rx_buf = b""

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        self.close()
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
        except OSError as exc:
            raise TransportError(f"connect to {self.host}:{self.port} failed: {exc}") from exc
        sock.settimeout(self.timeout_s)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass  # best-effort; not fatal if the platform doesn't support it
        self._sock = sock
        self._rx_buf = b""

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._rx_buf = b""

    def send_line(self, command: str) -> None:
        """Send one command (or ';'-joined group), appending the LF
        terminator."""
        if self._sock is None:
            raise TransportError("not connected")
        data = command.encode("ascii") + b"\n"
        try:
            self._sock.sendall(data)
        except OSError as exc:
            self.close()
            raise TransportError(f"send failed: {exc}") from exc

    def read_responses(self, count: int, timeout_s: float | None = None) -> str:
        """Read exactly `count` CRLF-terminated response lines and return
        them concatenated (including their CRLF terminators, so callers can
        use protocol.split_group_response on the result).

        Raises TransportError on timeout or socket failure. A timeout may
        leave partial data buffered for the *next* call, which the worker
        treats as a desync condition and recovers from by reconnecting.
        """
        if self._sock is None:
            raise TransportError("not connected")

        deadline = time.monotonic() + (timeout_s if timeout_s is not None else self.timeout_s)
        lines_found = self._rx_buf.count(RESPONSE_TERMINATOR)

        while lines_found < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(f"timed out waiting for {count} response(s), got {lines_found}")
            try:
                self._sock.settimeout(remaining)
                chunk = self._sock.recv(4096)
            except socket.timeout as exc:
                raise TransportError(f"timed out waiting for {count} response(s), got {lines_found}") from exc
            except OSError as exc:
                self.close()
                raise TransportError(f"recv failed: {exc}") from exc
            if not chunk:
                self.close()
                raise TransportError("connection closed by instrument")
            self._rx_buf += chunk
            lines_found = self._rx_buf.count(RESPONSE_TERMINATOR)

        # Split off exactly `count` terminated lines; keep any remainder
        # buffered (there shouldn't be any for well-formed traffic, but this
        # keeps us honest against a stray extra reply).
        parts = self._rx_buf.split(RESPONSE_TERMINATOR)
        # parts has len == lines_found + 1 (trailing remainder, "" if none)
        consumed = parts[:count]
        remainder_parts = parts[count:]
        self._rx_buf = RESPONSE_TERMINATOR.join(remainder_parts) if remainder_parts else b""
        blob = RESPONSE_TERMINATOR.join(consumed) + RESPONSE_TERMINATOR
        return blob.decode("ascii", errors="replace")

    def drain(self) -> None:
        """Best-effort non-blocking drain of any buffered/incoming bytes,
        used when resyncing after a framing error."""
        self._rx_buf = b""
        if self._sock is None:
            return
        self._sock.settimeout(0.05)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
        except OSError:
            pass
        finally:
            try:
                self._sock.settimeout(self.timeout_s)
            except OSError:
                pass
