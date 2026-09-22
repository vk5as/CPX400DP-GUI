"""Global (both-channel) controls: OPALL, CONFIG/VTRACK, RATIO, TRIPRST,
LOCAL, interface lock, *RST, and poll rate."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from psu.protocol import ConfigMode
from psu.worker import PsuWorker


class GlobalPanel(ttk.LabelFrame):
    def __init__(self, parent, worker: PsuWorker, on_status: callable, on_config_changing: callable):
        super().__init__(parent, text="Global", padding=8)
        self.worker = worker
        self.on_status = on_status
        # Called before we send CONFIG with an output on, so the app layer
        # can turn Output 2 off first (CONFIG errors with 104 otherwise).
        self.on_config_changing = on_config_changing
        self._build()

    def _build(self) -> None:
        row = 0
        out_row = ttk.Frame(self)
        out_row.grid(row=row, column=0, sticky="w", pady=2)
        ttk.Button(out_row, text="ALL ON", command=lambda: self.worker.set_output_all(True)).pack(side="left", padx=2)
        ttk.Button(out_row, text="ALL OFF", command=lambda: self.worker.set_output_all(False)).pack(side="left", padx=2)
        row += 1

        mode_row = ttk.LabelFrame(self, text="Mode", padding=4)
        mode_row.grid(row=row, column=0, sticky="w", pady=4)
        self.mode_var = tk.StringVar(value="independent")
        ttk.Radiobutton(mode_row, text="Independent", value="independent", variable=self.mode_var,
                        command=self._apply_mode).pack(anchor="w")
        ttk.Radiobutton(mode_row, text="Voltage Tracking", value="tracking", variable=self.mode_var,
                        command=self._apply_mode).pack(anchor="w")
        row += 1

        ratio_row = ttk.Frame(self)
        ratio_row.grid(row=row, column=0, sticky="w", pady=2)
        ttk.Label(ratio_row, text="Ratio").pack(side="left")
        self.ratio_var = tk.DoubleVar(value=100.0)
        ratio_scale = ttk.Scale(ratio_row, from_=0, to=100, variable=self.ratio_var,
                                 command=lambda _v: self._apply_ratio(), length=120)
        ratio_scale.pack(side="left", padx=4)
        self.ratio_label = ttk.Label(ratio_row, text="100%")
        self.ratio_label.pack(side="left")
        row += 1

        actions_row = ttk.Frame(self)
        actions_row.grid(row=row, column=0, sticky="w", pady=4)
        ttk.Button(actions_row, text="Clear Trips", command=self._clear_trips).pack(side="left", padx=2)
        ttk.Button(actions_row, text="Go Local", command=self.worker.go_local).pack(side="left", padx=2)
        ttk.Button(actions_row, text="*RST", command=self._reset).pack(side="left", padx=2)
        row += 1

        lock_row = ttk.Frame(self)
        lock_row.grid(row=row, column=0, sticky="w", pady=4)
        ttk.Button(lock_row, text="Lock Interface", command=self._lock).pack(side="left", padx=2)
        ttk.Button(lock_row, text="Unlock Interface", command=self._unlock).pack(side="left", padx=2)
        self.lock_status = ttk.Label(lock_row, text="unlocked")
        self.lock_status.pack(side="left", padx=6)
        row += 1

        poll_row = ttk.Frame(self)
        poll_row.grid(row=row, column=0, sticky="w", pady=4)
        ttk.Label(poll_row, text="Poll rate").pack(side="left")
        self.poll_var = tk.DoubleVar(value=2.0)
        poll_spin = ttk.Spinbox(poll_row, from_=0.5, to=4.0, increment=0.5, textvariable=self.poll_var, width=5,
                                 command=lambda: self.worker.set_poll_hz(self.poll_var.get()))
        poll_spin.pack(side="left", padx=4)
        ttk.Label(poll_row, text="Hz").pack(side="left")
        row += 1

    def _apply_mode(self) -> None:
        wants_independent = self.mode_var.get() == "independent"
        mode = ConfigMode.INDEPENDENT if wants_independent else ConfigMode.VOLTAGE_TRACKING
        self.on_config_changing()
        self.worker.set_config(mode)

    def _apply_ratio(self) -> None:
        pct = round(self.ratio_var.get())
        self.ratio_label.configure(text=f"{pct}%")
        self.worker.set_ratio(pct)

    def _clear_trips(self) -> None:
        self.worker.trip_reset()
        self.on_status("Trip conditions cleared")

    def _reset(self) -> None:
        if messagebox.askyesno(
            "Confirm *RST",
            "Reset the instrument to its remote-operation defaults?\n\n"
            "This sets Vout=1V, Iout=1A, DeltaV=10mV, DeltaI=10mA, cancels "
            "VTRACK, and resets OVP/OCP to 66V/22A on both channels. "
            "Output on/off state is not affected.",
        ):
            self.worker.reset_instrument()
            self.on_status("Instrument reset to remote defaults (*RST)")

    def _lock(self) -> None:
        self.worker.interface_lock()

    def _unlock(self) -> None:
        self.worker.interface_unlock()

    # -- updates from worker events -------------------------------------

    def apply_global_settings(self, config_independent: bool, ratio_percent: float) -> None:
        self.mode_var.set("independent" if config_independent else "tracking")
        self.ratio_var.set(ratio_percent)
        self.ratio_label.configure(text=f"{round(ratio_percent)}%")

    def apply_lock_state(self, owned_by_us: int) -> None:
        text = {1: "locked by us", 0: "unlocked", -1: "locked by another interface"}.get(owned_by_us, "unknown")
        self.lock_status.configure(text=text)

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for child in self.winfo_children():
            self._set_state_recursive(child, state)

    def _set_state_recursive(self, widget, state: str) -> None:
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_state_recursive(child, state)
