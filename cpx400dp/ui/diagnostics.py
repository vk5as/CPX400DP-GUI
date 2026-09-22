"""Register-level diagnostics and a raw command console.

This is what makes 'every control feature available' true even beyond the
buttons on the main panel: *ESR/EER/QER/*STB/LSR/LSE/*SRE/*PRE with a
bit-decoded view, plus a console that can send anything.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import scrolledtext, ttk

from cpx400dp.model import DiagnosticsUpdated, RawReply
from cpx400dp.ui.themes import LIGHT, Palette
from cpx400dp.worker import Cpx400dpWorker

ESR_BITS = {
    7: "Power On", 6: "User Request", 5: "Command Error", 4: "Execution Error",
    3: "Verify Timeout", 2: "Query Error", 1: "(unused)", 0: "Operation Complete",
}
STB_BITS = {
    6: "RQS/MSS", 5: "ESB", 4: "MAV", 1: "LIM2", 0: "LIM1",
}
LSR_BITS = {
    6: "Hard trip (front panel / AC cycle to clear)", 4: "Unregulated (power limit)",
    3: "Over-current trip", 2: "Over-voltage trip", 1: "Constant Current", 0: "Constant Voltage",
}


def _decode_bits(value: int, bit_names: dict[int, str]) -> str:
    set_bits = [name for bit, name in sorted(bit_names.items(), reverse=True) if value & (1 << bit)]
    return ", ".join(set_bits) if set_bits else "(none set)"


class DiagnosticsPanel(ttk.Frame):
    def __init__(self, parent, worker: Cpx400dpWorker):
        super().__init__(parent, padding=8)
        self.worker = worker
        self._palette = LIGHT
        self._build()

    def _build(self) -> None:
        reg_frame = ttk.LabelFrame(self, text="Status registers", padding=8)
        reg_frame.pack(side="top", fill="x", pady=(0, 8))

        self.reg_labels: dict[str, ttk.Label] = {}
        rows = [
            ("execution_error", "Execution Error (EER?)"),
            ("query_error", "Query Error (QER?)"),
            ("event_status", "Standard Event Status (*ESR?)"),
            ("status_byte", "Status Byte (*STB?)"),
            ("address", "Bus Address (ADDRESS?)"),
        ]
        for i, (key, label) in enumerate(rows):
            ttk.Label(reg_frame, text=label + ":").grid(row=i, column=0, sticky="w", padx=(0, 8))
            val = ttk.Label(reg_frame, text="—")
            val.grid(row=i, column=1, sticky="w")
            self.reg_labels[key] = val

        ttk.Button(reg_frame, text="Refresh", command=self.worker.refresh_diagnostics).grid(
            row=len(rows), column=0, sticky="w", pady=(6, 0)
        )

        lsr_frame = ttk.LabelFrame(self, text="Limit status (from live poll)", padding=8)
        lsr_frame.pack(side="top", fill="x", pady=(0, 8))
        self.lsr_labels: dict[int, ttk.Label] = {}
        for ch in (1, 2):
            ttk.Label(lsr_frame, text=f"Channel {ch}:").grid(row=ch - 1, column=0, sticky="nw", padx=(0, 8))
            val = ttk.Label(lsr_frame, text="—", wraplength=400, justify="left")
            val.grid(row=ch - 1, column=1, sticky="w")
            self.lsr_labels[ch] = val

        console_frame = ttk.LabelFrame(self, text="Raw command console", padding=8)
        console_frame.pack(side="top", fill="both", expand=True)

        entry_row = ttk.Frame(console_frame)
        entry_row.pack(side="top", fill="x")
        self.console_var = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.console_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._send_console())
        ttk.Button(entry_row, text="Send", command=self._send_console).pack(side="left", padx=(4, 0))
        ttk.Label(console_frame,
                  text="Any command from the manual, e.g. V1?, OP1 1, V1O?;I1O?, *IDN?").pack(
            side="top", anchor="w", pady=(2, 4))

        self.log = scrolledtext.ScrolledText(console_frame, height=12, state="disabled", font=("TkFixedFont",))
        self.log.pack(side="top", fill="both", expand=True)

    def _send_console(self) -> None:
        cmd = self.console_var.get().strip()
        if not cmd:
            return
        self.worker.send_raw(cmd)
        self.console_var.set("")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def apply_theme(self, palette: Palette) -> None:
        self._palette = palette
        self.log.configure(background=palette.field_bg, foreground=palette.foreground,
                            insertbackground=palette.foreground)

    # -- updates from worker events -------------------------------------

    def apply_diagnostics(self, evt: DiagnosticsUpdated) -> None:
        if evt.execution_error is not None:
            self.reg_labels["execution_error"].configure(text=str(evt.execution_error))
        if evt.query_error is not None:
            self.reg_labels["query_error"].configure(text=str(evt.query_error))
        if evt.event_status is not None:
            self.reg_labels["event_status"].configure(
                text=f"{evt.event_status} ({_decode_bits(evt.event_status, ESR_BITS)})")
        if evt.status_byte is not None:
            self.reg_labels["status_byte"].configure(
                text=f"{evt.status_byte} ({_decode_bits(evt.status_byte, STB_BITS)})")
        if evt.address is not None:
            self.reg_labels["address"].configure(text=str(evt.address))

    def apply_limit_status(self, channel: int, raw: int) -> None:
        self.lsr_labels[channel].configure(text=f"{raw} ({_decode_bits(raw, LSR_BITS)})")

    def apply_raw_reply(self, evt: RawReply) -> None:
        ts = time.strftime("%H:%M:%S")
        reply = evt.reply.replace("\r\n", " | ").strip(" |") or "(no reply)"
        self._append_log(f"{ts}  → {evt.request}\n{ts}  ← {reply}")
