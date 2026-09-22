"""Pure command builders and response parsers for the Aim-TTi CPX400DP.

No I/O lives here. Everything is a plain string in, plain value out, so this
module can be unit-tested without a socket or an instrument.

Wire format (from the CPX400D/DP Instruction Manual, Issue 1):
  - Commands are terminated with LF (0x0A). Several commands may be grouped
    on one line separated by ';', with a single trailing LF.
  - Responses are terminated with CR LF (0x0D 0x0A), one per query.
  - Commands are case-insensitive; whitespace is ignored except inside
    identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProtocolError(ValueError):
    """Raised when a response cannot be parsed as the expected reply."""


# ---------------------------------------------------------------------------
# Instrument limits (per the manual's Output Specifications table)
# ---------------------------------------------------------------------------

VOLTAGE_MIN = 0.0
VOLTAGE_MAX = 60.0
CURRENT_MIN = 0.0
CURRENT_MAX = 20.0
OVP_MIN = 1.0
OVP_MAX = 66.0
OCP_MIN = 0.0
OCP_MAX = 20.0
POWER_ENVELOPE_W = 420.0
RATIO_MIN = 0
RATIO_MAX = 100
STORE_MIN = 0
STORE_MAX = 9


class ConfigMode(Enum):
    INDEPENDENT = 2
    VOLTAGE_TRACKING = 0


def _fmt(value: float) -> str:
    """Render a float the way the instrument expects: plain decimal, no
    scientific notation, trimmed to a sane number of digits."""
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _channel(n: int) -> int:
    if n not in (1, 2):
        raise ValueError(f"channel must be 1 or 2, got {n!r}")
    return n


def _strip_rmt(line: str) -> str:
    """Strip the <RESPONSE MESSAGE TERMINATOR> (CR/LF) and surrounding
    whitespace a transport may have left on the line."""
    return line.strip("\r\n").strip()


# ---------------------------------------------------------------------------
# Command builders — per-channel
# ---------------------------------------------------------------------------

def set_voltage(channel: int, volts: float, verify: bool = False) -> str:
    n = _channel(channel)
    suffix = "V" if verify else ""
    return f"V{n}{suffix} {_fmt(volts)}"


def query_voltage(channel: int) -> str:
    return f"V{_channel(channel)}?"


def set_current(channel: int, amps: float) -> str:
    return f"I{_channel(channel)} {_fmt(amps)}"


def query_current(channel: int) -> str:
    return f"I{_channel(channel)}?"


def set_ovp(channel: int, volts: float) -> str:
    return f"OVP{_channel(channel)} {_fmt(volts)}"


def query_ovp(channel: int) -> str:
    return f"OVP{_channel(channel)}?"


def set_ocp(channel: int, amps: float) -> str:
    return f"OCP{_channel(channel)} {_fmt(amps)}"


def query_ocp(channel: int) -> str:
    return f"OCP{_channel(channel)}?"


def query_readback_voltage(channel: int) -> str:
    return f"V{_channel(channel)}O?"


def query_readback_current(channel: int) -> str:
    return f"I{_channel(channel)}O?"


def set_delta_v(channel: int, volts: float) -> str:
    return f"DELTAV{_channel(channel)} {_fmt(volts)}"


def query_delta_v(channel: int) -> str:
    return f"DELTAV{_channel(channel)}?"


def set_delta_i(channel: int, amps: float) -> str:
    return f"DELTAI{_channel(channel)} {_fmt(amps)}"


def query_delta_i(channel: int) -> str:
    return f"DELTAI{_channel(channel)}?"


def inc_voltage(channel: int, verify: bool = False) -> str:
    return f"INCV{_channel(channel)}{'V' if verify else ''}"


def dec_voltage(channel: int, verify: bool = False) -> str:
    return f"DECV{_channel(channel)}{'V' if verify else ''}"


def inc_current(channel: int) -> str:
    return f"INCI{_channel(channel)}"


def dec_current(channel: int) -> str:
    return f"DECI{_channel(channel)}"


def set_output(channel: int, on: bool) -> str:
    return f"OP{_channel(channel)} {1 if on else 0}"


def query_output(channel: int) -> str:
    return f"OP{_channel(channel)}?"


def save_setup(channel: int, store: int) -> str:
    if not (STORE_MIN <= store <= STORE_MAX):
        raise ValueError(f"store must be 0-9, got {store!r}")
    return f"SAV{_channel(channel)} {store}"


def recall_setup(channel: int, store: int) -> str:
    if not (STORE_MIN <= store <= STORE_MAX):
        raise ValueError(f"store must be 0-9, got {store!r}")
    return f"RCL{_channel(channel)} {store}"


def query_limit_status(channel: int) -> str:
    return f"LSR{_channel(channel)}?"


def set_limit_enable(channel: int, mask: int) -> str:
    return f"LSE{_channel(channel)} {mask}"


def query_limit_enable(channel: int) -> str:
    return f"LSE{_channel(channel)}?"


# ---------------------------------------------------------------------------
# Command builders — global
# ---------------------------------------------------------------------------

def set_output_all(on: bool) -> str:
    return f"OPALL {1 if on else 0}"


def set_config(mode: ConfigMode) -> str:
    return f"CONFIG {mode.value}"


def query_config() -> str:
    return "CONFIG?"


def set_ratio(percent: float) -> str:
    if not (RATIO_MIN <= percent <= RATIO_MAX):
        raise ValueError(f"ratio must be 0-100, got {percent!r}")
    return f"RATIO {_fmt(percent)}"


def query_ratio() -> str:
    return "RATIO?"


def trip_reset() -> str:
    return "TRIPRST"


def go_local() -> str:
    return "LOCAL"


def interface_lock() -> str:
    return "IFLOCK"


def query_interface_lock() -> str:
    return "IFLOCK?"


def interface_unlock() -> str:
    return "IFUNLOCK"


def query_execution_error() -> str:
    return "EER?"


def query_query_error() -> str:
    return "QER?"


def query_address() -> str:
    return "ADDRESS?"


# ---------------------------------------------------------------------------
# Command builders — IEEE 488.2 common commands
# ---------------------------------------------------------------------------

def idn() -> str:
    return "*IDN?"


def reset() -> str:
    return "*RST"


def clear_status() -> str:
    return "*CLS"


def query_event_status() -> str:
    return "*ESR?"


def set_event_status_enable(mask: int) -> str:
    return f"*ESE {mask}"


def query_event_status_enable() -> str:
    return "*ESE?"


def query_status_byte() -> str:
    return "*STB?"


def set_service_request_enable(mask: int) -> str:
    return f"*SRE {mask}"


def query_service_request_enable() -> str:
    return "*SRE?"


def set_parallel_poll_enable(mask: int) -> str:
    return f"*PRE {mask}"


def query_parallel_poll_enable() -> str:
    return "*PRE?"


def query_ist() -> str:
    return "*IST?"


def operation_complete() -> str:
    return "*OPC"


def query_operation_complete() -> str:
    return "*OPC?"


def self_test() -> str:
    return "*TST?"


def trigger() -> str:
    return "*TRG"


def wait() -> str:
    return "*WAI"


def group(*commands: str) -> str:
    """Combine several command units into one ';'-separated program message.

    The manual explicitly allows this; it is how the poll loop pipelines a
    whole cycle of queries into a single round trip.
    """
    if not commands:
        raise ValueError("group() requires at least one command")
    return ";".join(commands)


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def _parse_nr2_after_prefix(line: str, prefixes: tuple[str, ...]) -> float:
    """Parse a reply of the form '<PREFIX><n> <value>' where PREFIX is one of
    the given case-insensitive prefixes, returning the trailing numeric
    value. Used for V<n>?, I<n>?, OVP<n>?, OCP<n>?, DELTAV<n>?, DELTAI<n>?
    style replies, whose prefix in the reply is not always identical to the
    query's command name (e.g. OVP<n>? replies as VP<n>, OCP<n>? as CP<n>).
    """
    s = _strip_rmt(line)
    upper = s.upper()
    for prefix in prefixes:
        if upper.startswith(prefix):
            rest = s[len(prefix):]
            # rest is now "<n> <value>" — skip the channel digit(s) and space
            rest = rest.lstrip()
            parts = rest.split(None, 1)
            if len(parts) == 2:
                try:
                    return float(parts[1])
                except ValueError:
                    pass
            # some replies have no space between channel and value is not
            # expected, but be defensive
            break
    raise ProtocolError(f"could not parse reply {line!r} with prefixes {prefixes}")


def parse_voltage_setpoint(line: str) -> float:
    """Parse the reply to V<n>?, e.g. 'V1 12.000'."""
    return _parse_nr2_after_prefix(line, ("V",))


def parse_current_setpoint(line: str) -> float:
    """Parse the reply to I<n>?, e.g. 'I1 2.000'."""
    return _parse_nr2_after_prefix(line, ("I",))


def parse_ovp(line: str) -> float:
    """Parse the reply to OVP<n>?, e.g. 'VP1 66.0'."""
    return _parse_nr2_after_prefix(line, ("VP",))


def parse_ocp(line: str) -> float:
    """Parse the reply to OCP<n>?, e.g. 'CP1 22.00'."""
    return _parse_nr2_after_prefix(line, ("CP",))


def parse_delta_v(line: str) -> float:
    """Parse the reply to DELTAV<n>?, e.g. 'DELTAV1 0.010'."""
    return _parse_nr2_after_prefix(line, ("DELTAV",))


def parse_delta_i(line: str) -> float:
    """Parse the reply to DELTAI<n>?, e.g. 'DELTAI1 0.010'."""
    return _parse_nr2_after_prefix(line, ("DELTAI",))


def parse_readback_voltage(line: str) -> float:
    """Parse the reply to V<n>O?, e.g. '12.003V'."""
    s = _strip_rmt(line)
    upper = s.upper()
    if not upper.endswith("V"):
        raise ProtocolError(f"expected voltage readback ending in 'V', got {line!r}")
    try:
        return float(s[:-1])
    except ValueError as exc:
        raise ProtocolError(f"could not parse voltage readback {line!r}") from exc


def parse_readback_current(line: str) -> float:
    """Parse the reply to I<n>O?, e.g. '0.250A'."""
    s = _strip_rmt(line)
    upper = s.upper()
    if not upper.endswith("A"):
        raise ProtocolError(f"expected current readback ending in 'A', got {line!r}")
    try:
        return float(s[:-1])
    except ValueError as exc:
        raise ProtocolError(f"could not parse current readback {line!r}") from exc


def parse_bool01(line: str) -> bool:
    """Parse a bare '0' or '1' reply (OP<n>?)."""
    s = _strip_rmt(line)
    if s == "1":
        return True
    if s == "0":
        return False
    raise ProtocolError(f"expected '0' or '1', got {line!r}")


def parse_int(line: str) -> int:
    """Parse a bare integer reply (LSR<n>?, LSE<n>?, EER?, QER?, ADDRESS?,
    CONFIG?, *ESR?, *ESE?, *STB?, *SRE?, *PRE?, *IST?)."""
    s = _strip_rmt(line)
    try:
        return int(float(s))
    except ValueError as exc:
        raise ProtocolError(f"could not parse integer reply {line!r}") from exc


def parse_float(line: str) -> float:
    """Parse a bare float reply (RATIO?, *OPC? is technically int but bare)."""
    s = _strip_rmt(line)
    try:
        return float(s)
    except ValueError as exc:
        raise ProtocolError(f"could not parse float reply {line!r}") from exc


@dataclass(frozen=True)
class Identity:
    manufacturer: str
    model: str
    serial: str
    version: str


def parse_idn(line: str) -> Identity:
    """Parse the *IDN? reply: '<NAME>,<model>,<Serial No.>,<version>'."""
    s = _strip_rmt(line)
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 4:
        raise ProtocolError(f"malformed *IDN? reply {line!r}")
    return Identity(manufacturer=parts[0], model=parts[1], serial=parts[2], version=",".join(parts[3:]))


@dataclass(frozen=True)
class LimitStatus:
    """Decoded LSR<n>? bit field.

    Bit 0: CV mode. Bit 1: CC mode. Bit 2: OV trip. Bit 3: OC trip.
    Bit 4: unregulated (power limit). Bit 6: trip needing front-panel/AC
    power-cycle clear.
    """

    raw: int

    @property
    def constant_voltage(self) -> bool:
        return bool(self.raw & 0x01)

    @property
    def constant_current(self) -> bool:
        return bool(self.raw & 0x02)

    @property
    def over_voltage_trip(self) -> bool:
        return bool(self.raw & 0x04)

    @property
    def over_current_trip(self) -> bool:
        return bool(self.raw & 0x08)

    @property
    def unregulated(self) -> bool:
        return bool(self.raw & 0x10)

    @property
    def hard_trip(self) -> bool:
        return bool(self.raw & 0x40)

    @property
    def any_trip(self) -> bool:
        return self.over_voltage_trip or self.over_current_trip or self.hard_trip


def parse_limit_status(line: str) -> LimitStatus:
    return LimitStatus(raw=parse_int(line))


def split_group_response(blob: str, count: int) -> list[str]:
    """Split the concatenated CRLF-terminated replies to a pipelined command
    group into `count` individual lines.

    Raises ProtocolError if the number of lines does not match `count`,
    which the worker treats as a desync signal.
    """
    lines = [ln for ln in blob.split("\r\n") if ln != ""]
    if len(lines) != count:
        raise ProtocolError(
            f"expected {count} responses in group, got {len(lines)}: {lines!r}"
        )
    return lines
