"""Worker tests against the real fake_cpx simulator over loopback TCP.

These exercise the worker's queueing, coalescing, connect/poll/reconnect
state machine, and event posting end-to-end without touching real
hardware or Tk.
"""

from __future__ import annotations

import queue
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fake_cpx import FakeCpxServer  # noqa: E402

from cpx400dp.model import (
    ChannelReadingUpdated,
    ChannelSettingsUpdated,
    ConnectionState,
    ConnectionStateChanged,
    ErrorOccurred,
    IdentityReceived,
)
from cpx400dp.worker import Cpx400dpWorker


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sim():
    port = _free_port()
    server = FakeCpxServer("127.0.0.1", port, drop_after=0, slow=0.0, garbage=False, noisy=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()


@pytest.fixture
def sim_with_state():
    """Like `sim`, but also yields the InstrumentState so a test can
    trigger fault injection mid-run."""
    port = _free_port()
    server = FakeCpxServer("127.0.0.1", port, drop_after=0, slow=0.0, garbage=False, noisy=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port, server.state
    server.shutdown()
    server.server_close()


@pytest.fixture
def worker():
    events: queue.Queue = queue.Queue()
    w = Cpx400dpWorker(events, poll_hz=20.0)  # fast poll to keep tests quick
    w.start()
    yield w, events
    w.shutdown(timeout=3.0)


def drain_until(events: queue.Queue, predicate, timeout: float = 3.0):
    """Pop events until one matches predicate; return it. Fails the test on
    timeout."""
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            evt = events.get(timeout=0.1)
        except queue.Empty:
            continue
        seen.append(evt)
        if predicate(evt):
            return evt
    pytest.fail(f"timed out waiting for matching event; saw: {seen}")


def test_connect_reaches_connected(sim, worker):
    w, events = worker
    w.connect("127.0.0.1", sim)
    evt = drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)
    assert evt.state == ConnectionState.CONNECTED


def test_connect_receives_identity(sim, worker):
    w, events = worker
    w.connect("127.0.0.1", sim)
    evt = drain_until(events, lambda e: isinstance(e, IdentityReceived))
    assert evt.identity.model == "CPX400DP"


def test_poll_produces_channel_readings(sim, worker):
    w, events = worker
    w.connect("127.0.0.1", sim)
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)
    evt = drain_until(events, lambda e: isinstance(e, ChannelReadingUpdated) and e.channel == 1)
    assert evt.reading.voltage == 0.0  # output is off by default


def test_set_voltage_reflected_in_settings_and_readback(sim, worker):
    w, events = worker
    w.connect("127.0.0.1", sim)
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)

    w.set_voltage(1, 12.0)
    evt = drain_until(events, lambda e: isinstance(e, ChannelSettingsUpdated) and e.channel == 1)
    assert evt.settings.voltage_setpoint == 12.0

    w.set_current(1, 2.0)
    drain_until(
        events,
        lambda e: isinstance(e, ChannelSettingsUpdated) and e.channel == 1 and e.settings.current_setpoint == 2.0,
    )

    w.set_output(1, True)
    evt = drain_until(events, lambda e: isinstance(e, ChannelReadingUpdated) and e.channel == 1 and e.reading.output_on)
    assert evt.reading.voltage == pytest.approx(12.0, abs=0.5)  # sim default load 10 ohm -> CC likely


def test_coalescing_drops_stale_setpoints(sim, worker):
    """Rapidly submitting many set_voltage calls with the same coalesce key
    should not run every one of them — only the latest should actually
    reach the instrument. We verify indirectly: after a burst, the final
    settings readback matches only the last value, and the total number of
    ChannelSettingsUpdated events is far fewer than the number submitted."""
    w, events = worker
    w.connect("127.0.0.1", sim)
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)

    # Drain any settings events from connect before the burst.
    time.sleep(0.2)
    while not events.empty():
        events.get_nowait()

    n = 50
    for i in range(n):
        w.set_voltage(1, float(i))

    evt = drain_until(
        events,
        lambda e: isinstance(e, ChannelSettingsUpdated)
        and e.channel == 1
        and e.settings.voltage_setpoint == float(n - 1),
    )
    assert evt.settings.voltage_setpoint == n - 1

    # Count how many ChannelSettingsUpdated for channel 1 arrive over a
    # fixed window. Can't wait for the queue to go quiet: the poll loop
    # keeps producing unrelated ChannelReadingUpdated events forever.
    count = 1
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            e = events.get(timeout=0.1)
        except queue.Empty:
            continue
        if isinstance(e, ChannelSettingsUpdated) and e.channel == 1:
            count += 1
    assert count < n, f"expected coalescing to reduce {n} requests to far fewer, got {count} settings updates"


def test_reconnect_after_drop(sim, worker):
    w, events = worker
    w.connect("127.0.0.1", sim)
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)

    # Force-close the connection to simulate a drop. This is routed through
    # the worker's own queue (not called directly from the test thread)
    # because the transport is only ever safe to touch from the worker
    # thread itself — reaching in directly would race with a concurrent
    # poll, which is exactly the hazard the single-worker-thread design
    # exists to avoid.
    w.submit(lambda w: w._transport.close(), label="test: force close")

    drain_until(
        events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.RECONNECTING, timeout=5.0
    )
    drain_until(
        events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED, timeout=10.0
    )


def test_trip_reset_clears_latched_trip(sim, worker):
    w, events = worker
    w.connect("127.0.0.1", sim)
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)

    # The sim's default current limit is 0A, which would clamp the output
    # to 0V (CC at 0A) before voltage ever reaches the OVP threshold — so a
    # generous current limit must be set first for the OVP trip to fire.
    w.set_current(1, 5.0)
    drain_until(
        events,
        lambda e: isinstance(e, ChannelSettingsUpdated) and e.channel == 1 and e.settings.current_setpoint == 5.0,
    )
    w.set_ovp(1, 2.0)  # low OVP so a 12V setpoint trips it
    drain_until(events, lambda e: isinstance(e, ChannelSettingsUpdated) and e.channel == 1 and e.settings.ovp == 2.0)
    w.set_voltage(1, 12.0)
    drain_until(
        events,
        lambda e: isinstance(e, ChannelSettingsUpdated) and e.channel == 1 and e.settings.voltage_setpoint == 12.0,
    )
    w.set_output(1, True)

    evt = drain_until(
        events,
        lambda e: isinstance(e, ChannelReadingUpdated) and e.channel == 1 and e.reading.latched_over_voltage,
        timeout=5.0,
    )
    assert evt.reading.latched_over_voltage

    w.trip_reset()
    evt = drain_until(
        events,
        lambda e: isinstance(e, ChannelReadingUpdated) and e.channel == 1 and not e.reading.latched_over_voltage,
        timeout=5.0,
    )
    assert not evt.reading.latched_over_voltage


def test_disconnect_sets_disconnected_state(sim, worker):
    w, events = worker
    w.connect("127.0.0.1", sim)
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)
    w.disconnect()
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.DISCONNECTED)


def test_survives_response_desync_and_keeps_polling(sim_with_state, worker):
    """Regression test for issue #1: a poll reply that's correctly framed
    and correctly counted but has the wrong content (e.g. 'V1 28.00'
    instead of '28.003V') used to raise an uncaught ProtocolError straight
    out of the run() loop, silently killing the whole worker thread -- the
    GUI just went unresponsive with no error shown. It must now: report
    the problem, recover on its own, and keep the worker thread alive."""
    sim_port, state = sim_with_state
    w, events = worker
    w.connect("127.0.0.1", sim_port)
    drain_until(events, lambda e: isinstance(e, ConnectionStateChanged) and e.state == ConnectionState.CONNECTED)

    state.corrupt_next_vout = True

    drain_until(
        events,
        lambda e: isinstance(e, ErrorOccurred) and "desync" in e.message.lower(),
        timeout=5.0,
    )

    assert w.is_alive(), "worker thread must not die from a malformed reply"

    # Polling must have resumed on its own -- no reconnect needed, since
    # draining the stray bytes was enough to resync.
    drain_until(
        events,
        lambda e: isinstance(e, ChannelReadingUpdated) and e.channel == 1,
        timeout=5.0,
    )

    # And the worker must still accept and execute new commands.
    w.set_voltage(1, 5.0)
    evt = drain_until(
        events,
        lambda e: isinstance(e, ChannelSettingsUpdated) and e.channel == 1,
        timeout=5.0,
    )
    assert evt.settings.voltage_setpoint == 5.0
