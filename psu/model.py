"""Plain-data state shared between the worker thread and the GUI.

Everything here is either immutable (event dataclasses posted on evt_q) or a
simple mutable container that only the GUI thread mutates (DeviceState is
built fresh from events, never shared/written by the worker directly).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto

from psu.protocol import Identity, LimitStatus


class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()


@dataclass(frozen=True)
class ChannelSettings:
    """Setpoint-side state for one channel, read on connect and refreshed
    after writes. Not part of the fast poll loop."""

    voltage_setpoint: float = 0.0
    current_setpoint: float = 0.0
    ovp: float = 66.0
    ocp: float = 22.0
    delta_v: float = 0.01
    delta_i: float = 0.01


@dataclass(frozen=True)
class ChannelReading:
    """Fast-poll-loop state for one channel: readback + output + status."""

    voltage: float = 0.0
    current: float = 0.0
    output_on: bool = False
    limit_status: LimitStatus = field(default_factory=lambda: LimitStatus(raw=0))
    # Latched trip bits (see worker.py) — sticky until TRIPRST / user clear,
    # because LSR<n>? is read-and-clear on the instrument and would
    # otherwise flash for one poll cycle and vanish.
    latched_over_voltage: bool = False
    latched_over_current: bool = False
    latched_hard_trip: bool = False

    @property
    def power(self) -> float:
        return self.voltage * self.current


# ---------------------------------------------------------------------------
# Events posted by the worker onto evt_q. The GUI pump drains these and
# updates its own view of DeviceState; the worker never touches Tk.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConnectionStateChanged:
    state: ConnectionState
    detail: str = ""


@dataclass(frozen=True)
class IdentityReceived:
    identity: Identity


@dataclass(frozen=True)
class ChannelReadingUpdated:
    channel: int
    reading: ChannelReading
    timestamp: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class ChannelSettingsUpdated:
    channel: int
    settings: ChannelSettings


@dataclass(frozen=True)
class GlobalSettingsUpdated:
    config_independent: bool
    ratio_percent: float


@dataclass(frozen=True)
class InterfaceLockChanged:
    owned_by_us: int  # 1 owned, 0 free, -1 unavailable


@dataclass(frozen=True)
class DiagnosticsUpdated:
    execution_error: int | None = None
    query_error: int | None = None
    event_status: int | None = None
    status_byte: int | None = None
    address: int | None = None


@dataclass(frozen=True)
class RawReply:
    """Reply to a raw console command, or a general request/response log
    line for the status log."""

    request: str
    reply: str


@dataclass(frozen=True)
class ErrorOccurred:
    message: str


@dataclass(frozen=True)
class CommandAcked:
    """A fire-and-forget confirmation so the GUI can log what was sent even
    for commands with no query reply (e.g. OP1 1)."""

    request: str


# ---------------------------------------------------------------------------
# GUI-side aggregate view, rebuilt incrementally from the events above.
# ---------------------------------------------------------------------------

@dataclass
class DeviceState:
    connection: ConnectionState = ConnectionState.DISCONNECTED
    identity: Identity | None = None
    readings: dict[int, ChannelReading] = field(
        default_factory=lambda: {1: ChannelReading(), 2: ChannelReading()}
    )
    settings: dict[int, ChannelSettings] = field(
        default_factory=lambda: {1: ChannelSettings(), 2: ChannelSettings()}
    )
    config_independent: bool = True
    ratio_percent: float = 100.0
    interface_lock_owned: int = 0
    last_execution_error: int = 0
    last_query_error: int = 0
