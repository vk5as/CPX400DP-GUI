#!/usr/bin/env python3
"""Software simulator of the Aim-TTi CPX400DP LAN interface.

Implements the command set documented in the CPX400D/DP Instruction Manual
(Issue 1) closely enough to develop and test the GUI/worker without real
hardware: LF-framed commands (';'-grouped), CRLF-framed replies, per-channel
setpoints/OVP/OCP/deltas/output state, save/recall stores, CONFIG/RATIO,
interface lock, and the status-register model (EER/LSR read-and-clear,
*ESR, *STB).

A simple resistive load model per channel means readback is not just an
echo: if the load draws more current than the current-limit setpoint at the
given voltage, the channel drops into CC mode at I_set and V = I_set * R.

Usage:
    python tools/fake_cpx.py --port 9221
    python tools/fake_cpx.py --port 9221 --drop-after 50   # fault injection
"""

from __future__ import annotations

import argparse
import random
import re
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpx400dp.transport import DEFAULT_PORT  # noqa: E402


class ChannelSim:
    def __init__(self) -> None:
        self.v_set = 0.0
        self.i_set = 0.0
        self.ovp = 66.0
        self.ocp = 22.0
        self.delta_v = 0.01
        self.delta_i = 0.01
        self.output_on = False
        self.load_ohms = 10.0  # simulated load resistance
        self.stores: dict[int, dict] = {}
        self.lsr_pending = 0  # accumulated bits since last LSR<n>? read
        self.hard_trip = False

    def snapshot_store(self) -> dict:
        return {
            "v_set": self.v_set,
            "i_set": self.i_set,
            "ovp": self.ovp,
            "ocp": self.ocp,
            "delta_v": self.delta_v,
            "delta_i": self.delta_i,
        }

    def restore_store(self, snap: dict) -> None:
        self.v_set = snap["v_set"]
        self.i_set = snap["i_set"]
        self.ovp = snap["ovp"]
        self.ocp = snap["ocp"]
        self.delta_v = snap["delta_v"]
        self.delta_i = snap["delta_i"]

    def readback(self, noise: bool) -> tuple[float, float, int]:
        """Return (v_out, i_out, lsr_bits_now) applying the load model."""
        if not self.output_on or self.hard_trip:
            return 0.0, 0.0, (0x40 if self.hard_trip else 0)

        # Load model: if unlimited current would exceed i_set, we're in CC.
        i_unlimited = self.v_set / self.load_ohms if self.load_ohms > 0 else float("inf")
        bits = 0
        if i_unlimited > self.i_set:
            v_out = self.i_set * self.load_ohms
            i_out = self.i_set
            bits |= 0x02  # CC
        else:
            v_out = self.v_set
            i_out = i_unlimited
            bits |= 0x01  # CV

        if v_out > self.ovp:
            bits |= 0x04  # OV trip
            self.output_on = False
            self.hard_trip = False
            v_out, i_out = 0.0, 0.0
        if i_out > self.ocp:
            bits |= 0x08  # OC trip
            self.output_on = False
            v_out, i_out = 0.0, 0.0

        if noise:
            v_out += random.uniform(-0.003, 0.003)
            i_out += random.uniform(-0.002, 0.002)

        return max(v_out, 0.0), max(i_out, 0.0), bits


class InstrumentState:
    def __init__(self, noisy: bool) -> None:
        self.channels = {1: ChannelSim(), 2: ChannelSim()}
        self.config_independent = True
        self.ratio_percent = 100.0
        self.eer = 0
        self.esr = 128  # power-on bit set at startup
        self.lock_owner: object | None = None
        self.lock_guard = threading.Lock()
        self.noisy = noisy
        self.address = 11

    def set_error(self, code: int) -> None:
        self.eer = code
        self.esr |= 0x10  # Execution Error bit


class FakeCpxHandler(socketserver.StreamRequestHandler):
    server: "FakeCpxServer"

    def handle(self) -> None:
        state: InstrumentState = self.server.state
        buf = b""
        count = 0
        drop_after = self.server.drop_after
        slow = self.server.slow
        garbage = self.server.garbage
        while True:
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                count += 1
                text = line.decode("ascii", errors="replace").strip("\r")
                if not text:
                    continue
                if drop_after and count > drop_after:
                    self.connection.close()
                    return
                if slow:
                    time.sleep(slow)
                reply = self._process_line(state, text)
                if garbage and count % 7 == 0:
                    reply = "??garbled??"
                if reply:
                    try:
                        self.connection.sendall(reply.encode("ascii"))
                    except OSError:
                        return

    def _process_line(self, state: InstrumentState, line: str) -> str:
        replies = []
        for unit in line.split(";"):
            unit = unit.strip()
            if not unit:
                continue
            reply = self._dispatch(state, unit)
            if reply is not None:
                replies.append(reply + "\r\n")
        return "".join(replies)

    # Ordered (regex, handler) table. Order matters where prefixes overlap
    # (e.g. "OVP1" vs "OP1", "V1O?" vs "V1?") — longest/most-specific
    # patterns are listed first within each group.
    _PATTERNS: list[tuple[re.Pattern, str]] = [
        (re.compile(r"^V([12])O\?$"), "read_vout"),
        (re.compile(r"^I([12])O\?$"), "read_iout"),
        (re.compile(r"^V([12])V\s+(.+)$"), "set_v_verify"),
        (re.compile(r"^V([12])\s+(.+)$"), "set_v"),
        (re.compile(r"^V([12])\?$"), "query_v"),
        (re.compile(r"^I([12])\s+(.+)$"), "set_i"),
        (re.compile(r"^I([12])\?$"), "query_i"),
        (re.compile(r"^OVP([12])\s+(.+)$"), "set_ovp"),
        (re.compile(r"^OVP([12])\?$"), "query_ovp"),
        (re.compile(r"^OCP([12])\s+(.+)$"), "set_ocp"),
        (re.compile(r"^OCP([12])\?$"), "query_ocp"),
        (re.compile(r"^DELTAV([12])\s+(.+)$"), "set_delta_v"),
        (re.compile(r"^DELTAV([12])\?$"), "query_delta_v"),
        (re.compile(r"^DELTAI([12])\s+(.+)$"), "set_delta_i"),
        (re.compile(r"^DELTAI([12])\?$"), "query_delta_i"),
        (re.compile(r"^INCV([12])V$"), "inc_v_verify"),
        (re.compile(r"^INCV([12])$"), "inc_v"),
        (re.compile(r"^DECV([12])V$"), "dec_v_verify"),
        (re.compile(r"^DECV([12])$"), "dec_v"),
        (re.compile(r"^INCI([12])$"), "inc_i"),
        (re.compile(r"^DECI([12])$"), "dec_i"),
        (re.compile(r"^OPALL\s+(\d)$"), "set_op_all"),
        (re.compile(r"^OP([12])\s+(\d)$"), "set_op"),
        (re.compile(r"^OP([12])\?$"), "query_op"),
        (re.compile(r"^SAV([12])\s+(\d)$"), "save"),
        (re.compile(r"^RCL([12])\s+(\d)$"), "recall"),
        (re.compile(r"^LSR([12])\?$"), "query_lsr"),
        (re.compile(r"^LSE([12])\s+(.+)$"), "set_lse"),
        (re.compile(r"^LSE([12])\?$"), "query_lse"),
        (re.compile(r"^CONFIG\?$"), "query_config"),
        (re.compile(r"^CONFIG\s+(.+)$"), "set_config"),
        (re.compile(r"^RATIO\?$"), "query_ratio"),
        (re.compile(r"^RATIO\s+(.+)$"), "set_ratio"),
        (re.compile(r"^TRIPRST$"), "triprst"),
        (re.compile(r"^LOCAL$"), "local"),
        (re.compile(r"^IFLOCK\?$"), "query_iflock"),
        (re.compile(r"^IFLOCK$"), "iflock"),
        (re.compile(r"^IFUNLOCK$"), "ifunlock"),
        (re.compile(r"^EER\?$"), "eer"),
        (re.compile(r"^QER\?$"), "qer"),
        (re.compile(r"^ADDRESS\?$"), "address"),
        (re.compile(r"^\*IDN\?$"), "idn"),
        (re.compile(r"^\*RST$"), "rst"),
        (re.compile(r"^\*CLS$"), "cls"),
        (re.compile(r"^\*ESR\?$"), "esr"),
        (re.compile(r"^\*ESE\?$"), "ese_q"),
        (re.compile(r"^\*ESE\s+(.+)$"), "ese_set"),
        (re.compile(r"^\*STB\?$"), "stb"),
        (re.compile(r"^\*SRE\?$"), "sre_q"),
        (re.compile(r"^\*SRE\s+(.+)$"), "sre_set"),
        (re.compile(r"^\*PRE\?$"), "pre_q"),
        (re.compile(r"^\*PRE\s+(.+)$"), "pre_set"),
        (re.compile(r"^\*IST\?$"), "ist"),
        (re.compile(r"^\*OPC\?$"), "opc_q"),
        (re.compile(r"^\*OPC$"), "opc"),
        (re.compile(r"^\*TST\?$"), "tst"),
        (re.compile(r"^\*TRG$"), "trg"),
        (re.compile(r"^\*WAI$"), "wai"),
    ]

    def _dispatch(self, state: InstrumentState, cmd: str) -> str | None:  # noqa: C901
        upper = cmd.upper().strip()

        for pattern, name in self._PATTERNS:
            m = pattern.match(upper)
            if m:
                return self._handle(state, name, m)
        return None  # unknown/typo commands are silently ignored

    # Command names whose regex's first capture group is the channel number
    # (1 or 2), as opposed to a numeric argument that happens to be "1"/"2".
    _CHANNEL_SCOPED = {
        "read_vout", "read_iout", "set_v_verify", "set_v", "query_v",
        "set_i", "query_i", "set_ovp", "query_ovp", "set_ocp", "query_ocp",
        "set_delta_v", "query_delta_v", "set_delta_i", "query_delta_i",
        "inc_v", "inc_v_verify", "dec_v", "dec_v_verify", "inc_i", "dec_i",
        "set_op", "query_op", "save", "recall", "query_lsr", "set_lse",
        "query_lse",
    }

    def _handle(self, state: InstrumentState, name: str, m: "re.Match") -> str | None:  # noqa: C901
        groups = m.groups()
        n = int(groups[0]) if name in self._CHANNEL_SCOPED else None
        ch = state.channels[n] if n is not None else None

        try:
            if name == "read_vout":
                v, i, bits = ch.readback(state.noisy)
                ch.lsr_pending |= bits
                return f"{v:.3f}V"
            if name == "read_iout":
                v, i, bits = ch.readback(state.noisy)
                return f"{i:.3f}A"
            if name == "set_v_verify" or name == "set_v":
                ch.v_set = float(groups[1])
                return None
            if name == "query_v":
                return f"V{n} {ch.v_set:.3f}"
            if name == "set_i":
                ch.i_set = float(groups[1])
                return None
            if name == "query_i":
                return f"I{n} {ch.i_set:.3f}"
            if name == "set_ovp":
                ch.ovp = float(groups[1])
                return None
            if name == "query_ovp":
                return f"VP{n} {ch.ovp:.3f}"
            if name == "set_ocp":
                ch.ocp = float(groups[1])
                return None
            if name == "query_ocp":
                return f"CP{n} {ch.ocp:.3f}"
            if name == "set_delta_v":
                ch.delta_v = float(groups[1])
                return None
            if name == "query_delta_v":
                return f"DELTAV{n} {ch.delta_v:.3f}"
            if name == "set_delta_i":
                ch.delta_i = float(groups[1])
                return None
            if name == "query_delta_i":
                return f"DELTAI{n} {ch.delta_i:.3f}"
            if name in ("inc_v", "inc_v_verify"):
                ch.v_set += ch.delta_v
                return None
            if name in ("dec_v", "dec_v_verify"):
                ch.v_set = max(0.0, ch.v_set - ch.delta_v)
                return None
            if name == "inc_i":
                ch.i_set += ch.delta_i
                return None
            if name == "dec_i":
                ch.i_set = max(0.0, ch.i_set - ch.delta_i)
                return None
            if name == "set_op_all":
                on = groups[0] == "1"
                for c in state.channels.values():
                    c.output_on = on
                return None
            if name == "set_op":
                ch.output_on = groups[1] == "1"
                ch.hard_trip = False
                return None
            if name == "query_op":
                return f"{1 if ch.output_on else 0}"
            if name == "save":
                store = int(groups[1])
                ch.stores[store] = ch.snapshot_store()
                return None
            if name == "recall":
                store = int(groups[1])
                snap = ch.stores.get(store)
                if snap is None:
                    state.set_error(102)
                else:
                    ch.restore_store(snap)
                return None
            if name == "query_lsr":
                bits = ch.lsr_pending
                ch.lsr_pending = 0
                return f"{bits}"
            if name == "set_lse":
                return None
            if name == "query_lse":
                return "0"
            if name == "query_config":
                return "2" if state.config_independent else "0"
            if name == "set_config":
                any_output_on = any(c.output_on for c in state.channels.values())
                if any_output_on:
                    state.set_error(104)
                else:
                    state.config_independent = groups[0].strip() == "2"
                return None

            if name == "query_ratio":
                return f"{state.ratio_percent:.1f}"
            if name == "set_ratio":
                state.ratio_percent = float(groups[0])
                return None

            if name == "triprst":
                for c in state.channels.values():
                    c.hard_trip = False
                return None
            if name == "local":
                return None

            if name == "iflock":
                with state.lock_guard:
                    if state.lock_owner is None or state.lock_owner == self.client_address:
                        state.lock_owner = self.client_address
                        return "1"
                    return "-1"
            if name == "query_iflock":
                with state.lock_guard:
                    if state.lock_owner is None:
                        return "0"
                    return "1" if state.lock_owner == self.client_address else "-1"
            if name == "ifunlock":
                with state.lock_guard:
                    if state.lock_owner == self.client_address:
                        state.lock_owner = None
                        return "0"
                    return "-1"

            if name == "eer":
                v = state.eer
                state.eer = 0
                return f"{v}"
            if name == "qer":
                return "0"
            if name == "address":
                return f"{state.address}"

            if name == "idn":
                return "THURLBY THANDAR, CPX400DP, SIM0001, 2.01-3.04"
            if name == "rst":
                for c in state.channels.values():
                    c.v_set = 1.0
                    c.i_set = 1.0
                    c.delta_v = 0.01
                    c.delta_i = 0.01
                    c.ovp = 66.0
                    c.ocp = 22.0
                state.ratio_percent = 100.0
                return None
            if name == "cls":
                state.esr = 0
                return None
            if name == "esr":
                v = state.esr
                state.esr = 0
                return f"{v}"
            if name in ("ese_q",):
                return "0"
            if name == "ese_set":
                return None
            if name == "stb":
                return "0"
            if name == "sre_q":
                return "0"
            if name == "sre_set":
                return None
            if name == "pre_q":
                return "0"
            if name == "pre_set":
                return None
            if name == "ist":
                return "0"
            if name == "opc":
                return None
            if name == "opc_q":
                return "1"
            if name == "tst":
                return "0"
            if name == "trg":
                return None
            if name == "wai":
                return None

            return None  # unreachable: every pattern above has a handler
        except (ValueError, IndexError, KeyError):
            state.set_error(100)
            return None


class FakeCpxServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, drop_after: int, slow: float, garbage: bool, noisy: bool):
        self.state = InstrumentState(noisy=noisy)
        self.drop_after = drop_after
        self.slow = slow
        self.garbage = garbage
        super().__init__((host, port), FakeCpxHandler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--drop-after", type=int, default=0, help="close connection after N commands")
    ap.add_argument("--slow", type=float, default=0.0, help="artificial delay per command, seconds")
    ap.add_argument("--garbage", action="store_true", help="occasionally reply with garbage")
    ap.add_argument("--noisy", action="store_true", default=True, help="add small noise to readback")
    ap.add_argument("--no-noisy", dest="noisy", action="store_false")
    args = ap.parse_args()

    server = FakeCpxServer(args.host, args.port, args.drop_after, args.slow, args.garbage, args.noisy)
    print(f"fake_cpx listening on {args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
