"""Command-line interface for one-shot / scripted control of the CPX400DP.

Unlike the GUI's PsuWorker, this opens a single TCP connection, performs one
operation, and exits — there's no persistent polling thread, no event queue,
and no Tk. It's meant for shell scripts and quick checks from a terminal,
e.g.:

    cpx400dp-cli --host 192.168.0.100 voltage 1 12.0
    cpx400dp-cli --host 192.168.0.100 output 1 on
    cpx400dp-cli --host 192.168.0.100 read 1
"""

from __future__ import annotations

import argparse
import sys

from cpx400dp import protocol as proto
from cpx400dp.transport import DEFAULT_PORT, DEFAULT_TIMEOUT_S, TcpTransport, TransportError


def _channel_type(value: str) -> int:
    ch = int(value)
    if ch not in (1, 2):
        raise argparse.ArgumentTypeError("channel must be 1 or 2")
    return ch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpx400dp-cli",
        description="Scripted control of an Aim-TTi CPX400DP over its LAN interface.",
    )
    parser.add_argument("--host", required=True, help="instrument IP address or hostname")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="seconds")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("idn", help="print instrument identity")

    p_v = sub.add_parser("voltage", help="get, or set, the voltage setpoint")
    p_v.add_argument("channel", type=_channel_type)
    p_v.add_argument("value", type=float, nargs="?", help="omit to query the current setpoint")
    p_v.add_argument("--verify", action="store_true", help="wait for the output to settle (V<n>V)")

    p_i = sub.add_parser("current", help="get, or set, the current limit")
    p_i.add_argument("channel", type=_channel_type)
    p_i.add_argument("value", type=float, nargs="?", help="omit to query the current setpoint")

    p_ovp = sub.add_parser("ovp", help="get, or set, the over-voltage protection trip point")
    p_ovp.add_argument("channel", type=_channel_type)
    p_ovp.add_argument("value", type=float, nargs="?")

    p_ocp = sub.add_parser("ocp", help="get, or set, the over-current protection trip point")
    p_ocp.add_argument("channel", type=_channel_type)
    p_ocp.add_argument("value", type=float, nargs="?")

    p_out = sub.add_parser("output", help="get, or set, output on/off")
    p_out.add_argument("channel", type=_channel_type)
    p_out.add_argument("state", choices=["on", "off"], nargs="?", help="omit to query")

    p_read = sub.add_parser("read", help="print live voltage/current readback (V<n>O?/I<n>O?)")
    p_read.add_argument("channel", type=_channel_type)

    p_status = sub.add_parser("status", help="print decoded limit/trip status (LSR<n>?, read-and-clear)")
    p_status.add_argument("channel", type=_channel_type)

    sub.add_parser("all-on", help="turn both outputs on (OPALL 1)")
    sub.add_parser("all-off", help="turn both outputs off (OPALL 0)")
    sub.add_parser("trip-reset", help="attempt to clear trip conditions (TRIPRST)")
    sub.add_parser("local", help="return control to the front panel (LOCAL)")
    sub.add_parser("reset", help="reset to remote-operation defaults (*RST)")

    p_raw = sub.add_parser("raw", help="send a raw command, or ';'-separated group, and print any reply")
    p_raw.add_argument("raw_command")

    return parser


def _query(transport: TcpTransport, command: str) -> str:
    transport.send_line(command)
    return proto.split_group_response(transport.read_responses(1), 1)[0]


def _query_group(transport: TcpTransport, *commands: str) -> list[str]:
    transport.send_line(proto.group(*commands))
    return proto.split_group_response(transport.read_responses(len(commands)), len(commands))


def _dispatch(transport: TcpTransport, args: argparse.Namespace) -> None:  # noqa: C901
    cmd = args.command

    if cmd == "idn":
        idn = proto.parse_idn(_query(transport, proto.idn()))
        print(f"{idn.manufacturer}, {idn.model}, SN:{idn.serial}, v{idn.version}")

    elif cmd == "voltage":
        if args.value is None:
            print(f"{proto.parse_voltage_setpoint(_query(transport, proto.query_voltage(args.channel))):.3f}")
        else:
            transport.send_line(proto.set_voltage(args.channel, args.value, verify=args.verify))

    elif cmd == "current":
        if args.value is None:
            print(f"{proto.parse_current_setpoint(_query(transport, proto.query_current(args.channel))):.3f}")
        else:
            transport.send_line(proto.set_current(args.channel, args.value))

    elif cmd == "ovp":
        if args.value is None:
            print(f"{proto.parse_ovp(_query(transport, proto.query_ovp(args.channel))):.3f}")
        else:
            transport.send_line(proto.set_ovp(args.channel, args.value))

    elif cmd == "ocp":
        if args.value is None:
            print(f"{proto.parse_ocp(_query(transport, proto.query_ocp(args.channel))):.3f}")
        else:
            transport.send_line(proto.set_ocp(args.channel, args.value))

    elif cmd == "output":
        if args.state is None:
            on = proto.parse_bool01(_query(transport, proto.query_output(args.channel)))
            print("on" if on else "off")
        else:
            transport.send_line(proto.set_output(args.channel, args.state == "on"))

    elif cmd == "read":
        v_reply, i_reply = _query_group(
            transport, proto.query_readback_voltage(args.channel), proto.query_readback_current(args.channel)
        )
        v = proto.parse_readback_voltage(v_reply)
        i = proto.parse_readback_current(i_reply)
        print(f"V={v:.3f} I={i:.3f}")

    elif cmd == "status":
        ls = proto.parse_limit_status(_query(transport, proto.query_limit_status(args.channel)))
        flags = [name for name, is_set in (
            ("CV", ls.constant_voltage), ("CC", ls.constant_current),
            ("OV_TRIP", ls.over_voltage_trip), ("OC_TRIP", ls.over_current_trip),
            ("UNREGULATED", ls.unregulated), ("HARD_TRIP", ls.hard_trip),
        ) if is_set]
        print(", ".join(flags) if flags else "(none)")

    elif cmd == "all-on":
        transport.send_line(proto.set_output_all(True))
    elif cmd == "all-off":
        transport.send_line(proto.set_output_all(False))
    elif cmd == "trip-reset":
        transport.send_line(proto.trip_reset())
    elif cmd == "local":
        transport.send_line(proto.go_local())
    elif cmd == "reset":
        transport.send_line(proto.reset())

    elif cmd == "raw":
        units = [u.strip() for u in args.raw_command.split(";") if u.strip()]
        expected = sum(1 for u in units if u.endswith("?"))
        transport.send_line(args.raw_command)
        if expected:
            blob = transport.read_responses(expected)
            print(blob.replace("\r\n", "\n").rstrip("\n"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    transport = TcpTransport(args.host, args.port, timeout_s=args.timeout)
    try:
        transport.connect()
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        _dispatch(transport, args)
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except proto.ProtocolError as exc:
        print(f"error: unexpected reply from instrument: {exc}", file=sys.stderr)
        return 1
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
