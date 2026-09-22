"""The single thread that ever touches the instrument socket.

The GUI never calls into transport.py or protocol.py directly. It calls
Cpx400dpWorker.submit(...) (or one of the convenience methods) to enqueue work,
and drains worker.events (a plain queue.Queue) on a Tk `after()` timer to
learn what happened. This keeps every blocking call off the Tk thread and
keeps the instrument's read-and-clear registers meaningful (see LSR<n>?
handling below), since only one socket, owned by one thread, ever talks to
the instrument.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from cpx400dp import protocol as proto
from cpx400dp.model import (
    ChannelReading,
    ChannelReadingUpdated,
    ChannelSettings,
    ChannelSettingsUpdated,
    CommandAcked,
    ConnectionState,
    ConnectionStateChanged,
    DiagnosticsUpdated,
    ErrorOccurred,
    GlobalSettingsUpdated,
    IdentityReceived,
    InterfaceLockChanged,
    RawReply,
    WorkerEvent,
)
from cpx400dp.protocol import LimitStatus
from cpx400dp.transport import TcpTransport, TransportError

BACKOFF_INITIAL_S = 0.5
BACKOFF_MAX_S = 8.0
CONSECUTIVE_FAILURES_BEFORE_RECONNECT = 3


@dataclass
class _Request:
    seq: int
    coalesce_key: str | None
    action: Callable[[Cpx400dpWorker], None]
    label: str = ""


class Cpx400dpWorker(threading.Thread):
    """Owns the TCP connection and all command/response traffic.

    Public API is intentionally small and non-blocking: submit(), connect(),
    disconnect(), set_poll_hz(), shutdown(). Everything else happens via
    events posted to `self.events`.
    """

    def __init__(self, events: queue.Queue[WorkerEvent], poll_hz: float = 2.0, poll_limit_status: bool = True):
        super().__init__(name="Cpx400dpWorker", daemon=True)
        self.events: queue.Queue[WorkerEvent] = events
        # A plain FIFO: the periodic poll is never enqueued here (it runs
        # from the loop's own timeout when due), so anything a user does
        # always jumps ahead of it without needing a priority queue.
        self._cmd_q: queue.Queue[_Request] = queue.Queue()
        self._seq = itertools.count()
        # Latest sequence number submitted per coalesce_key. A popped
        # request whose seq no longer matches this has been superseded by a
        # newer one with the same key and is skipped rather than run — this
        # is what stops a dragged slider or held arrow key from building an
        # unbounded backlog of stale setpoints.
        self._latest_seq_for_key: dict[str, int] = {}
        self._stop_evt = threading.Event()

        self._transport: TcpTransport | None = None
        self._host: str | None = None
        self._port: int = 9221
        self._state = ConnectionState.DISCONNECTED
        self._consecutive_failures = 0
        self._backoff_s = BACKOFF_INITIAL_S

        self._poll_hz = poll_hz
        self._poll_limit_status = poll_limit_status
        self._poll_interval_s = 1.0 / poll_hz if poll_hz > 0 else 0.5
        self._next_poll_due = 0.0

        # Latched trip bits per channel — see model.ChannelReading docstring.
        self._latched: dict[int, dict[str, bool]] = {
            1: {"ov": False, "oc": False, "hard": False},
            2: {"ov": False, "oc": False, "hard": False},
        }

        self._request_lock_on_connect = False

    # -- public API, callable from the GUI thread -------------------------

    def submit(
        self, action: Callable[[Cpx400dpWorker], None], *, coalesce_key: str | None = None, label: str = ""
    ) -> None:
        """Enqueue a user-initiated action."""
        seq = next(self._seq)
        if coalesce_key is not None:
            self._latest_seq_for_key[coalesce_key] = seq
        self._cmd_q.put(_Request(seq, coalesce_key, action, label))

    def connect(self, host: str, port: int = 9221, request_lock: bool = False) -> None:
        self._request_lock_on_connect = request_lock
        self.submit(lambda w: w._do_connect(host, port), label=f"connect {host}:{port}")

    def disconnect(self) -> None:
        self.submit(lambda w: w._do_disconnect(), label="disconnect")

    def set_poll_hz(self, hz: float) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._poll_hz = hz
            w._poll_interval_s = 1.0 / hz if hz > 0 else 0.5

        self.submit(_apply, label=f"set_poll_hz {hz}")

    def set_poll_limit_status(self, enabled: bool) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._poll_limit_status = enabled

        self.submit(_apply, label=f"set_poll_limit_status {enabled}")

    def clear_latched_trips(self, channel: int | None = None) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            channels = [channel] if channel else [1, 2]
            for ch in channels:
                w._latched[ch] = {"ov": False, "oc": False, "hard": False}

        self.submit(_apply, label="clear_latched_trips")

    def send_raw(self, command: str) -> None:
        """Send an arbitrary command/group typed into the diagnostics
        console. If it looks like a query (ends with '?' on any of its
        ';'-separated units), read back that many responses."""

        def _apply(w: Cpx400dpWorker) -> None:
            units = [u.strip() for u in command.split(";") if u.strip()]
            expected = sum(1 for u in units if u.endswith("?"))
            try:
                w._send(command)
                reply = w._read(expected) if expected else ""
            except TransportError as exc:
                w._on_transport_error(exc)
                return
            w._post(RawReply(request=command, reply=reply))

        self.submit(_apply, label=f"raw {command}")

    def shutdown(self, timeout: float = 3.0) -> None:
        self._stop_evt.set()
        self._cmd_q.put(_Request(next(self._seq), None, lambda w: None, "wake"))
        self.join(timeout=timeout)

    # -- convenience command builders (still just enqueue, non-blocking) --

    def set_voltage(self, channel: int, volts: float, verify: bool = False) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_voltage(channel, volts, verify))
            w._post(CommandAcked(request=f"V{channel}={volts}"))
            w._refresh_channel_settings(channel)

        self.submit(_apply, coalesce_key=f"set_v{channel}", label=f"set_voltage {channel} {volts}")

    def set_current(self, channel: int, amps: float) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_current(channel, amps))
            w._post(CommandAcked(request=f"I{channel}={amps}"))
            w._refresh_channel_settings(channel)

        self.submit(_apply, coalesce_key=f"set_i{channel}", label=f"set_current {channel} {amps}")

    def set_ovp(self, channel: int, volts: float) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_ovp(channel, volts))
            w._refresh_channel_settings(channel)

        self.submit(_apply, coalesce_key=f"set_ovp{channel}", label=f"set_ovp {channel} {volts}")

    def set_ocp(self, channel: int, amps: float) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_ocp(channel, amps))
            w._refresh_channel_settings(channel)

        self.submit(_apply, coalesce_key=f"set_ocp{channel}", label=f"set_ocp {channel} {amps}")

    def set_delta_v(self, channel: int, volts: float) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_delta_v(channel, volts))
            w._refresh_channel_settings(channel)

        self.submit(_apply, coalesce_key=f"set_dv{channel}", label=f"set_delta_v {channel} {volts}")

    def set_delta_i(self, channel: int, amps: float) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_delta_i(channel, amps))
            w._refresh_channel_settings(channel)

        self.submit(_apply, coalesce_key=f"set_di{channel}", label=f"set_delta_i {channel} {amps}")

    def inc_voltage(self, channel: int) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.inc_voltage(channel))
            w._refresh_channel_settings(channel)

        self.submit(_apply, label=f"inc_voltage {channel}")

    def dec_voltage(self, channel: int) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.dec_voltage(channel))
            w._refresh_channel_settings(channel)

        self.submit(_apply, label=f"dec_voltage {channel}")

    def inc_current(self, channel: int) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.inc_current(channel))
            w._refresh_channel_settings(channel)

        self.submit(_apply, label=f"inc_current {channel}")

    def dec_current(self, channel: int) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.dec_current(channel))
            w._refresh_channel_settings(channel)

        self.submit(_apply, label=f"dec_current {channel}")

    def set_output(self, channel: int, on: bool) -> None:
        self.submit(
            lambda w: w._send(proto.set_output(channel, on)),
            coalesce_key=f"set_op{channel}",
            label=f"set_output {channel} {on}",
        )

    def set_output_all(self, on: bool) -> None:
        self.submit(lambda w: w._send(proto.set_output_all(on)), label=f"set_output_all {on}")

    def save_setup(self, channel: int, store: int) -> None:
        self.submit(lambda w: w._send(proto.save_setup(channel, store)), label=f"save_setup {channel} {store}")

    def recall_setup(self, channel: int, store: int) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.recall_setup(channel, store))
            w._refresh_channel_settings(channel)

        self.submit(_apply, label=f"recall_setup {channel} {store}")

    def set_config(self, mode: proto.ConfigMode) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_config(mode))
            w._refresh_global_settings()

        self.submit(_apply, label=f"set_config {mode}")

    def set_ratio(self, percent: float) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.set_ratio(percent))
            w._refresh_global_settings()

        self.submit(_apply, coalesce_key="set_ratio", label=f"set_ratio {percent}")

    def trip_reset(self) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.trip_reset())
            for ch in (1, 2):
                w._latched[ch] = {"ov": False, "oc": False, "hard": False}

        self.submit(_apply, label="trip_reset")

    def go_local(self) -> None:
        self.submit(lambda w: w._send(proto.go_local()), label="go_local")

    def interface_lock(self) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            reply = w._query(proto.interface_lock())
            w._post(InterfaceLockChanged(owned_by_us=proto.parse_int(reply)))

        self.submit(_apply, label="interface_lock")

    def interface_unlock(self) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._query(proto.interface_unlock())
            w._post(InterfaceLockChanged(owned_by_us=0))

        self.submit(_apply, label="interface_unlock")

    def reset_instrument(self) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            w._send(proto.reset())
            w._refresh_all_settings()

        self.submit(_apply, label="reset_instrument")

    def refresh_diagnostics(self) -> None:
        def _apply(w: Cpx400dpWorker) -> None:
            eer = proto.parse_int(w._query(proto.query_execution_error()))
            qer = proto.parse_int(w._query(proto.query_query_error()))
            esr = proto.parse_int(w._query(proto.query_event_status()))
            stb = proto.parse_int(w._query(proto.query_status_byte()))
            addr = proto.parse_int(w._query(proto.query_address()))
            w._post(
                DiagnosticsUpdated(
                    execution_error=eer, query_error=qer, event_status=esr, status_byte=stb, address=addr
                )
            )

        self.submit(_apply, label="refresh_diagnostics")

    # -- thread body --------------------------------------------------------

    def run(self) -> None:
        while not self._stop_evt.is_set():
            timeout = self._time_until_next_poll() if self._state == ConnectionState.CONNECTED else None
            try:
                req = self._cmd_q.get(timeout=timeout)
            except queue.Empty:
                self._maybe_poll()
                continue

            if self._stop_evt.is_set():
                break

            if req.coalesce_key is not None and self._latest_seq_for_key.get(req.coalesce_key) != req.seq:
                continue  # superseded by a newer request with the same key; drop it unrun

            try:
                req.action(self)
            except TransportError as exc:
                self._on_transport_error(exc)
            except Exception as exc:  # noqa: BLE001 — surface anything unexpected, keep the thread alive
                self._post(ErrorOccurred(message=f"{req.label or 'command'} failed: {exc}"))

            self._maybe_poll()

        if self._transport is not None:
            self._transport.close()

    def _time_until_next_poll(self) -> float | None:
        if self._state != ConnectionState.CONNECTED:
            return None
        remaining = self._next_poll_due - time.monotonic()
        return max(0.0, remaining)

    def _maybe_poll(self) -> None:
        if self._state != ConnectionState.CONNECTED:
            return
        now = time.monotonic()
        if now < self._next_poll_due:
            return
        self._next_poll_due = now + self._poll_interval_s
        try:
            self._poll_once()
            self._consecutive_failures = 0
        except TransportError as exc:
            self._on_transport_error(exc)

    def _poll_once(self) -> None:
        cmds = [
            proto.query_readback_voltage(1),
            proto.query_readback_current(1),
            proto.query_readback_voltage(2),
            proto.query_readback_current(2),
            proto.query_output(1),
            proto.query_output(2),
        ]
        if self._poll_limit_status:
            cmds += [proto.query_limit_status(1), proto.query_limit_status(2)]

        blob = self._query_group(cmds)

        v1 = proto.parse_readback_voltage(blob[0])
        i1 = proto.parse_readback_current(blob[1])
        v2 = proto.parse_readback_voltage(blob[2])
        i2 = proto.parse_readback_current(blob[3])
        op1 = proto.parse_bool01(blob[4])
        op2 = proto.parse_bool01(blob[5])

        ls1 = proto.parse_limit_status(blob[6]) if self._poll_limit_status else LimitStatus(raw=0)
        ls2 = proto.parse_limit_status(blob[7]) if self._poll_limit_status else LimitStatus(raw=0)

        self._update_latches(1, ls1)
        self._update_latches(2, ls2)

        self._post(
            ChannelReadingUpdated(
                1,
                ChannelReading(
                    voltage=v1,
                    current=i1,
                    output_on=op1,
                    limit_status=ls1,
                    latched_over_voltage=self._latched[1]["ov"],
                    latched_over_current=self._latched[1]["oc"],
                    latched_hard_trip=self._latched[1]["hard"],
                ),
            )
        )
        self._post(
            ChannelReadingUpdated(
                2,
                ChannelReading(
                    voltage=v2,
                    current=i2,
                    output_on=op2,
                    limit_status=ls2,
                    latched_over_voltage=self._latched[2]["ov"],
                    latched_over_current=self._latched[2]["oc"],
                    latched_hard_trip=self._latched[2]["hard"],
                ),
            )
        )

    def _update_latches(self, channel: int, ls: LimitStatus) -> None:
        latch = self._latched[channel]
        if ls.over_voltage_trip:
            latch["ov"] = True
        if ls.over_current_trip:
            latch["oc"] = True
        if ls.hard_trip:
            latch["hard"] = True

    def _refresh_channel_settings(self, channel: int) -> None:
        v = proto.parse_voltage_setpoint(self._query(proto.query_voltage(channel)))
        i = proto.parse_current_setpoint(self._query(proto.query_current(channel)))
        ovp = proto.parse_ovp(self._query(proto.query_ovp(channel)))
        ocp = proto.parse_ocp(self._query(proto.query_ocp(channel)))
        dv = proto.parse_delta_v(self._query(proto.query_delta_v(channel)))
        di = proto.parse_delta_i(self._query(proto.query_delta_i(channel)))
        self._post(
            ChannelSettingsUpdated(
                channel,
                ChannelSettings(
                    voltage_setpoint=v,
                    current_setpoint=i,
                    ovp=ovp,
                    ocp=ocp,
                    delta_v=dv,
                    delta_i=di,
                ),
            )
        )

    def _refresh_global_settings(self) -> None:
        cfg = proto.parse_int(self._query(proto.query_config()))
        ratio = proto.parse_float(self._query(proto.query_ratio()))
        self._post(
            GlobalSettingsUpdated(config_independent=(cfg == proto.ConfigMode.INDEPENDENT.value), ratio_percent=ratio)
        )

    def _refresh_all_settings(self) -> None:
        self._refresh_channel_settings(1)
        self._refresh_channel_settings(2)
        self._refresh_global_settings()

    # -- connection management ---------------------------------------------

    def _do_connect(self, host: str, port: int) -> None:
        self._host, self._port = host, port
        self._connect_now()

    def _connect_now(self) -> None:
        # Callers (_do_connect, _enter_reconnect) both guarantee _host is
        # set before reaching here; this just makes that invariant explicit
        # for mypy. Not a security control -- fine if stripped under -O,
        # since TcpTransport would then just fail loudly on a None host.
        assert self._host is not None  # nosec B101
        self._set_state(ConnectionState.CONNECTING)
        self._transport = TcpTransport(self._host, self._port)
        try:
            self._transport.connect()
            idn_reply = self._query(proto.idn())
            identity = proto.parse_idn(idn_reply)
            self._post(IdentityReceived(identity))
            if self._request_lock_on_connect:
                reply = self._query(proto.interface_lock())
                self._post(InterfaceLockChanged(owned_by_us=proto.parse_int(reply)))
            self._refresh_all_settings()
            self._consecutive_failures = 0
            self._backoff_s = BACKOFF_INITIAL_S
            self._next_poll_due = time.monotonic()
            self._set_state(ConnectionState.CONNECTED)
        except TransportError as exc:
            self._enter_reconnect(str(exc))

    def _do_disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
        self._set_state(ConnectionState.DISCONNECTED)

    def _on_transport_error(self, exc: TransportError) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= CONSECUTIVE_FAILURES_BEFORE_RECONNECT:
            self._enter_reconnect(str(exc))
        else:
            self._post(
                ErrorOccurred(
                    message=f"transient error ({self._consecutive_failures}/"
                    f"{CONSECUTIVE_FAILURES_BEFORE_RECONNECT}): {exc}"
                )
            )

    def _enter_reconnect(self, detail: str) -> None:
        if self._transport is not None:
            self._transport.close()
        self._set_state(ConnectionState.RECONNECTING, detail)
        self._consecutive_failures = 0
        # Sleep here is fine: this method only runs on the worker thread,
        # and no other work should proceed until we're back online.
        wait_s = self._backoff_s
        self._backoff_s = min(self._backoff_s * 2, BACKOFF_MAX_S)
        slept = 0.0
        step = 0.1
        while slept < wait_s and not self._stop_evt.is_set():
            time.sleep(step)
            slept += step
        if self._stop_evt.is_set() or self._host is None:
            return
        self._connect_now()

    def _set_state(self, state: ConnectionState, detail: str = "") -> None:
        self._state = state
        self._post(ConnectionStateChanged(state, detail))

    # -- low-level I/O helpers ----------------------------------------------

    def _send(self, command: str) -> None:
        if self._transport is None:
            raise TransportError("not connected")
        self._transport.send_line(command)

    def _read(self, count: int) -> str:
        if self._transport is None:
            raise TransportError("not connected")
        return self._transport.read_responses(count)

    def _query(self, command: str) -> str:
        """Send one command that expects exactly one reply line and return
        that line (without CRLF)."""
        self._send(command)
        blob = self._read(1)
        return proto.split_group_response(blob, 1)[0]

    def _query_group(self, commands: list[str]) -> list[str]:
        self._send(proto.group(*commands))
        blob = self._read(len(commands))
        try:
            return proto.split_group_response(blob, len(commands))
        except proto.ProtocolError as exc:
            if self._transport is not None:
                self._transport.drain()
            raise TransportError(f"response desync: {exc}") from exc

    def _post(self, event: WorkerEvent) -> None:
        self.events.put(event)
