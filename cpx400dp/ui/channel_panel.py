"""One channel's control + readback panel. Instantiated twice (channel 1
and 2). Talks to the instrument only through the Cpx400dpWorker handed to it —
never touches a socket directly."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from cpx400dp.model import ChannelReading, ChannelSettings
from cpx400dp.protocol import (
    CURRENT_MAX,
    CURRENT_MIN,
    OCP_MAX,
    OCP_MIN,
    OVP_MAX,
    OVP_MIN,
    POWER_ENVELOPE_W,
    STORE_MAX,
    STORE_MIN,
    VOLTAGE_MAX,
    VOLTAGE_MIN,
)
from cpx400dp.ui.themes import LIGHT, Palette
from cpx400dp.ui.widgets import Led, NumericEntry, ValueDisplay
from cpx400dp.worker import Cpx400dpWorker


class ChannelPanel(ttk.LabelFrame):
    def __init__(self, parent: tk.Misc, channel: int, worker: Cpx400dpWorker, on_status: Callable[[str], None]):
        super().__init__(parent, text=f"Output {channel}", padding=8)
        self.channel = channel
        self.worker = worker
        self.on_status = on_status
        self._palette = LIGHT

        self._build()

    def _build(self) -> None:
        ch = self.channel

        readback_row = ttk.Frame(self)
        readback_row.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.voltage_display = ValueDisplay(readback_row, "V")
        self.voltage_display.pack(side="left", padx=(0, 16))
        self.current_display = ValueDisplay(readback_row, "A")
        self.current_display.pack(side="left", padx=(0, 16))
        self.power_display = ValueDisplay(readback_row, "W", decimals=2)
        self.power_display.pack(side="left")

        self.mode_row = ttk.Frame(self)
        self.mode_row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._mode_leds: dict[str, Led] = {}
        for key, text in (("cv", "CV"), ("cc", "CC"), ("unreg", "UNREG"), ("trip", "TRIP")):
            frame = ttk.Frame(self.mode_row)
            frame.pack(side="left", padx=(0, 10))
            led = Led(frame, background=self._palette.background)
            led.pack(side="left")
            ttk.Label(frame, text=text).pack(side="left", padx=(3, 0))
            self._mode_leds[key] = led

        self.voltage_entry = NumericEntry(
            self,
            "Set V",
            "V",
            VOLTAGE_MIN,
            VOLTAGE_MAX,
            decimals=3,
            on_commit=lambda v: self._set_voltage(v),
        )
        self.voltage_entry.grid(row=2, column=0, sticky="w", pady=2)
        self.verify_var = tk.BooleanVar(value=False)
        self.verify_check = ttk.Checkbutton(self, text="verify", variable=self.verify_var)
        self.verify_check.grid(row=2, column=1, sticky="w")
        self.v_btns = ttk.Frame(self)
        self.v_btns.grid(row=2, column=2, sticky="w")
        ttk.Button(self.v_btns, text="▲", width=2, command=lambda: self.worker.inc_voltage(ch)).pack(side="left")
        ttk.Button(self.v_btns, text="▼", width=2, command=lambda: self.worker.dec_voltage(ch)).pack(side="left")

        self.current_entry = NumericEntry(
            self,
            "Set I",
            "A",
            CURRENT_MIN,
            CURRENT_MAX,
            decimals=3,
            on_commit=lambda a: self.worker.set_current(ch, a),
        )
        self.current_entry.grid(row=3, column=0, sticky="w", pady=2)
        self.i_btns = ttk.Frame(self)
        self.i_btns.grid(row=3, column=2, sticky="w")
        ttk.Button(self.i_btns, text="▲", width=2, command=lambda: self.worker.inc_current(ch)).pack(side="left")
        ttk.Button(self.i_btns, text="▼", width=2, command=lambda: self.worker.dec_current(ch)).pack(side="left")

        self.delta_row = ttk.Frame(self)
        self.delta_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=2)
        self.delta_v_entry = NumericEntry(
            self.delta_row,
            "ΔV",
            "V",
            0.01,
            VOLTAGE_MAX,
            decimals=3,
            on_commit=lambda v: self.worker.set_delta_v(ch, v),
            width=6,
        )
        self.delta_v_entry.pack(side="left", padx=(0, 10))
        self.delta_i_entry = NumericEntry(
            self.delta_row,
            "ΔI",
            "A",
            0.001,
            CURRENT_MAX,
            decimals=3,
            on_commit=lambda a: self.worker.set_delta_i(ch, a),
            width=6,
        )
        self.delta_i_entry.pack(side="left")

        self.prot_row = ttk.Frame(self)
        self.prot_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
        self.ovp_entry = NumericEntry(
            self.prot_row,
            "OVP",
            "V",
            OVP_MIN,
            OVP_MAX,
            decimals=1,
            on_commit=lambda v: self.worker.set_ovp(ch, v),
            width=6,
        )
        self.ovp_entry.pack(side="left", padx=(0, 10))
        self.ocp_entry = NumericEntry(
            self.prot_row,
            "OCP",
            "A",
            OCP_MIN,
            OCP_MAX,
            decimals=2,
            on_commit=lambda a: self.worker.set_ocp(ch, a),
            width=6,
        )
        self.ocp_entry.pack(side="left")

        out_row = ttk.Frame(self)
        out_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self.output_var = tk.BooleanVar(value=False)
        self.output_btn = ttk.Checkbutton(
            out_row,
            text="OUTPUT ON",
            variable=self.output_var,
            style="Toolbutton",
            command=self._toggle_output,
        )
        self.output_btn.pack(side="left")

        self.store_row = ttk.Frame(self)
        self.store_row.grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(self.store_row, text="Store").pack(side="left")
        self.store_var = tk.IntVar(value=0)
        store_spin = ttk.Spinbox(self.store_row, from_=STORE_MIN, to=STORE_MAX, textvariable=self.store_var, width=3)
        store_spin.pack(side="left", padx=4)
        ttk.Button(self.store_row, text="Save", command=self._save).pack(side="left", padx=2)
        ttk.Button(self.store_row, text="Recall", command=self._recall).pack(side="left", padx=2)

        self.envelope_warning = ttk.Label(self, text="", foreground=self._palette.warning)
        self.envelope_warning.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # Rows hidden in Compact Mode, leaving only readback (V/A), Set V,
        # Set I, and the output switch — the bare minimum to monitor and
        # drive a channel.
        self._compact_grid_widgets = (
            self.mode_row,
            self.verify_check,
            self.v_btns,
            self.i_btns,
            self.delta_row,
            self.prot_row,
            self.store_row,
            self.envelope_warning,
        )

    # -- user actions --------------------------------------------------

    def _set_voltage(self, volts: float) -> None:
        self.worker.set_voltage(self.channel, volts, verify=self.verify_var.get())
        self._check_envelope()

    def _toggle_output(self) -> None:
        self.worker.set_output(self.channel, self.output_var.get())

    def _save(self) -> None:
        self.worker.save_setup(self.channel, self.store_var.get())
        self.on_status(f"Saved output {self.channel} settings to store {self.store_var.get()}")

    def _recall(self) -> None:
        self.worker.recall_setup(self.channel, self.store_var.get())
        self.on_status(f"Recalled output {self.channel} settings from store {self.store_var.get()}")

    def _check_envelope(self) -> None:
        v = self.voltage_entry._value
        i = self.current_entry._value
        if v * i > POWER_ENVELOPE_W:
            self.envelope_warning.configure(
                text=f"⚠ {v:.1f}V × {i:.2f}A = {v * i:.0f}W exceeds the "
                f"{POWER_ENVELOPE_W:.0f}W envelope — output will be unregulated"
            )
        else:
            self.envelope_warning.configure(text="")

    # -- updates from worker events -------------------------------------

    def apply_reading(self, reading: ChannelReading) -> None:
        self.voltage_display.set(reading.voltage)
        self.current_display.set(reading.current)
        self.power_display.set(reading.power)

        ls = reading.limit_status
        self._mode_leds["cv"].set_color("green" if ls.constant_voltage else "off")
        self._mode_leds["cc"].set_color("amber" if ls.constant_current else "off")
        self._mode_leds["unreg"].set_color("red" if ls.unregulated else "off")
        any_trip = reading.latched_over_voltage or reading.latched_over_current or reading.latched_hard_trip
        self._mode_leds["trip"].set_color("red" if any_trip else "off")

        if self.output_var.get() != reading.output_on:
            self.output_var.set(reading.output_on)

    def apply_settings(self, settings: ChannelSettings) -> None:
        self.voltage_entry.set_external_value(settings.voltage_setpoint)
        self.current_entry.set_external_value(settings.current_setpoint)
        self.ovp_entry.set_external_value(settings.ovp)
        self.ocp_entry.set_external_value(settings.ocp)
        self.delta_v_entry.set_external_value(settings.delta_v)
        self.delta_i_entry.set_external_value(settings.delta_i)
        self._check_envelope()

    def set_compact(self, compact: bool) -> None:
        """Hide (or restore) everything except V/A readback, Set V, Set I,
        and the output switch — the bare minimum to monitor and drive a
        channel."""
        for widget in self._compact_grid_widgets:
            if compact:
                widget.grid_remove()
            else:
                widget.grid()
        if compact:
            self.power_display.pack_forget()
        else:
            self.power_display.pack(side="left")

    def apply_theme(self, palette: Palette) -> None:
        self._palette = palette
        for led in self._mode_leds.values():
            led.set_background(palette.background)
        self.envelope_warning.configure(foreground=palette.warning)

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for child in self.winfo_children():
            self._set_state_recursive(child, state)

    def _set_state_recursive(self, widget: tk.Misc, state: str) -> None:
        try:
            # Not every widget class supports the "state" option (e.g.
            # plain Frames/Labels don't) — that's caught below, so this is
            # deliberately outside what the static configure() overloads model.
            widget.configure(state=state)  # type: ignore[call-arg]
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_state_recursive(child, state)
