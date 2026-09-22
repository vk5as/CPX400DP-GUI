"""Top-level window: connection bar, two channel panels, global panel,
chart, status log, and a diagnostics tab. Owns the root.after() pump that
drains the worker's event queue — this is the only place events cross from
the worker thread's data into Tk state.
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import ttk

from psu.config import AppConfig
from psu.history import History
from psu.model import (
    ChannelReadingUpdated,
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
)
from psu.ui.channel_panel import ChannelPanel
from psu.ui.chart_panel import ChartPanel
from psu.ui.diagnostics import DiagnosticsPanel
from psu.ui.global_panel import GlobalPanel
from psu.ui.themes import LIGHT, THEME_NAMES, apply_theme, get_palette
from psu.worker import PsuWorker

EVENT_PUMP_INTERVAL_MS = 50
CHART_REFRESH_INTERVAL_MS = 200
MAX_EVENTS_PER_PUMP = 200


class MainWindow:
    def __init__(self, root: tk.Tk, config: AppConfig):
        self.root = root
        self.config = config
        self.events: "queue.Queue" = queue.Queue()
        self.worker = PsuWorker(self.events, poll_hz=config.poll_hz, poll_limit_status=config.poll_limit_status)
        self.history = History(max_points=config.history_max_points)
        self._latest = {1: {"v": 0.0, "i": 0.0}, 2: {"v": 0.0, "i": 0.0}}
        self._connection_state = ConnectionState.DISCONNECTED

        root.title("CPX400DP Control")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._build_menu()
        self._apply_theme(self.config.theme)
        self._apply_compact(self.config.compact_mode)
        self.worker.start()

        self.root.after(EVENT_PUMP_INTERVAL_MS, self._pump_events)
        self.root.after(CHART_REFRESH_INTERVAL_MS, self._refresh_chart)

    def _build(self) -> None:
        conn_bar = ttk.Frame(self.root, padding=6)
        conn_bar.pack(side="top", fill="x")

        ttk.Label(conn_bar, text="Host").pack(side="left")
        self.host_var = tk.StringVar(value=self.config.host)
        ttk.Entry(conn_bar, textvariable=self.host_var, width=16).pack(side="left", padx=4)
        ttk.Label(conn_bar, text="Port").pack(side="left")
        self.port_var = tk.IntVar(value=self.config.port)
        ttk.Entry(conn_bar, textvariable=self.port_var, width=6).pack(side="left", padx=4)
        self.connect_btn = ttk.Button(conn_bar, text="Connect", command=self._on_connect_clicked)
        self.connect_btn.pack(side="left", padx=4)

        self.conn_status_var = tk.StringVar(value="● DISCONNECTED")
        self.conn_status_label = ttk.Label(conn_bar, textvariable=self.conn_status_var)
        self.conn_status_label.pack(side="left", padx=12)
        self.idn_var = tk.StringVar(value="")
        self.idn_label = ttk.Label(conn_bar, textvariable=self.idn_var)
        self.idn_label.pack(side="left", padx=8)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True)

        main_tab = ttk.Frame(self.notebook)
        self.notebook.add(main_tab, text="Control")
        self.diag_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.diag_tab, text="Diagnostics")

        panels_row = ttk.Frame(main_tab)
        panels_row.pack(side="top", fill="x", padx=6, pady=6)

        self.channel_panels = {
            1: ChannelPanel(panels_row, 1, self.worker, self._set_status),
            2: ChannelPanel(panels_row, 2, self.worker, self._set_status),
        }
        self.channel_panels[1].pack(side="left", fill="y", padx=(0, 6))
        self.channel_panels[2].pack(side="left", fill="y", padx=(0, 6))

        self.global_panel = GlobalPanel(panels_row, self.worker, self._set_status, self._on_config_changing)
        self.global_panel.pack(side="left", fill="y")

        self.chart_panel = ChartPanel(main_tab, self.history)
        self.chart_panel.pack(side="top", fill="both", expand=True, padx=6, pady=(0, 6))

        status_frame = ttk.Frame(main_tab)
        status_frame.pack(side="bottom", fill="x", padx=6, pady=(0, 6))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(status_frame, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill="x")

        self.diagnostics_panel = DiagnosticsPanel(self.diag_tab, self.worker)
        self.diagnostics_panel.pack(fill="both", expand=True)

        self._set_channel_controls_enabled(False)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        self.root.configure(menu=menubar)

        view_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="View", menu=view_menu)

        theme_menu = tk.Menu(view_menu, tearoff=False)
        view_menu.add_cascade(label="Theme", menu=theme_menu)
        self.theme_var = tk.StringVar(value=self.config.theme)
        for name in THEME_NAMES:
            theme_menu.add_radiobutton(label=name, value=name, variable=self.theme_var,
                                        command=lambda n=name: self._apply_theme(n))

        self.compact_var = tk.BooleanVar(value=self.config.compact_mode)
        view_menu.add_checkbutton(label="Compact Mode", variable=self.compact_var,
                                   command=lambda: self._apply_compact(self.compact_var.get()))

    def _apply_theme(self, name: str) -> None:
        apply_theme(self.root, name)
        self.config.theme = name
        if hasattr(self, "theme_var"):
            self.theme_var.set(name)
        # Raw (non-ttk) widgets — the LED canvases, the diagnostics log, and
        # the matplotlib chart — don't follow ttk styling and must be
        # recolored explicitly. 'System' has no fixed palette of its own
        # (it defers to the OS theme), so these fall back to Light, which
        # is what every current native ttk theme actually looks like.
        palette = get_palette(name) or LIGHT
        self.channel_panels[1].apply_theme(palette)
        self.channel_panels[2].apply_theme(palette)
        self.diagnostics_panel.apply_theme(palette)
        self.chart_panel.apply_theme(palette)

    def _apply_compact(self, enabled: bool) -> None:
        """Compact Mode is the bare minimum: per-channel V/A readback, Set
        V, Set I, and output on/off. Everything else — mode LEDs, deltas,
        OVP/OCP, save/recall, the whole Global panel, the IDN string, and
        the chart — is hidden, not just shrunk."""
        self.config.compact_mode = enabled
        if hasattr(self, "compact_var"):
            self.compact_var.set(enabled)
        self.channel_panels[1].set_compact(enabled)
        self.channel_panels[2].set_compact(enabled)
        if enabled:
            self.global_panel.pack_forget()
            self.chart_panel.pack_forget()
            self.idn_label.pack_forget()
            # ttk.Notebook sizes itself to its LARGEST tab regardless of
            # which one is selected, not just the visible one — so with
            # Diagnostics still present the window couldn't shrink below
            # its size even with Control fully compacted. forget() (unlike
            # hide()) fully unmanages the tab, excluding it from that
            # sizing calculation; add() re-attaches the same frame later.
            self.notebook.forget(self.diag_tab)
        else:
            self.global_panel.pack(side="left", fill="y")
            self.chart_panel.pack(side="top", fill="both", expand=True, padx=6, pady=(0, 6))
            self.idn_label.pack(side="left", padx=8)
            self.notebook.add(self.diag_tab, text="Diagnostics")
        # Tk locks a toplevel's size once it's been drawn — hiding child
        # widgets alone leaves dead space rather than shrinking the window.
        # Clearing the explicit geometry lets it snap back to whatever size
        # the now-visible content actually needs.
        self.root.update_idletasks()
        self.root.geometry("")

    # -- connection bar ---------------------------------------------------

    def _on_connect_clicked(self) -> None:
        if self._connection_state in (ConnectionState.CONNECTED, ConnectionState.RECONNECTING,
                                       ConnectionState.CONNECTING):
            self.worker.disconnect()
            return
        host = self.host_var.get().strip()
        port = self.port_var.get()
        self._set_status(f"Connecting to {host}:{port}...")
        self.worker.connect(host, port)

    def _on_config_changing(self) -> None:
        """CONFIG errors (104) if an output is on. Turn Output 2 off first,
        matching the manual's requirement, and tell the user why."""
        if self.channel_panels[2].output_var.get():
            self.worker.set_output(2, False)
            self._set_status("Turned Output 2 off before changing operating mode (required by the instrument).")

    def _set_status(self, text: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.status_var.set(f"[{ts}] {text}")

    def _set_channel_controls_enabled(self, enabled: bool) -> None:
        self.channel_panels[1].set_enabled(enabled)
        self.channel_panels[2].set_enabled(enabled)
        self.global_panel.set_enabled(enabled)

    # -- event pump ---------------------------------------------------------

    def _pump_events(self) -> None:
        processed = 0
        try:
            while processed < MAX_EVENTS_PER_PUMP:
                evt = self.events.get_nowait()
                self._handle_event(evt)
                processed += 1
        except queue.Empty:
            pass
        self.root.after(EVENT_PUMP_INTERVAL_MS, self._pump_events)

    def _handle_event(self, evt) -> None:  # noqa: C901
        if isinstance(evt, ConnectionStateChanged):
            self._apply_connection_state(evt)
        elif isinstance(evt, IdentityReceived):
            idn = evt.identity
            self.idn_var.set(f"{idn.manufacturer} {idn.model} SN:{idn.serial} v{idn.version}")
        elif isinstance(evt, ChannelReadingUpdated):
            self.channel_panels[evt.channel].apply_reading(evt.reading)
            self._latest[evt.channel]["v"] = evt.reading.voltage
            self._latest[evt.channel]["i"] = evt.reading.current
            self.history.add(self._latest[1]["v"], self._latest[1]["i"],
                              self._latest[2]["v"], self._latest[2]["i"])
            if self.diagnostics_panel is not None:
                self.diagnostics_panel.apply_limit_status(evt.channel, evt.reading.limit_status.raw)
        elif isinstance(evt, ChannelSettingsUpdated):
            self.channel_panels[evt.channel].apply_settings(evt.settings)
        elif isinstance(evt, GlobalSettingsUpdated):
            self.global_panel.apply_global_settings(evt.config_independent, evt.ratio_percent)
        elif isinstance(evt, InterfaceLockChanged):
            self.global_panel.apply_lock_state(evt.owned_by_us)
        elif isinstance(evt, DiagnosticsUpdated):
            self.diagnostics_panel.apply_diagnostics(evt)
        elif isinstance(evt, RawReply):
            self.diagnostics_panel.apply_raw_reply(evt)
        elif isinstance(evt, CommandAcked):
            pass  # available for a future verbose log; not surfaced by default
        elif isinstance(evt, ErrorOccurred):
            self._set_status(evt.message)

    def _apply_connection_state(self, evt: ConnectionStateChanged) -> None:
        label = {
            ConnectionState.DISCONNECTED: "● DISCONNECTED",
            ConnectionState.CONNECTING: "● CONNECTING...",
            ConnectionState.CONNECTED: "● CONNECTED",
            ConnectionState.RECONNECTING: "● RECONNECTING...",
        }[evt.state]
        self.conn_status_var.set(label)
        self._connection_state = evt.state
        self._set_channel_controls_enabled(evt.state == ConnectionState.CONNECTED)
        if evt.state == ConnectionState.CONNECTED:
            self.connect_btn.configure(text="Disconnect")
            self._set_status("Connected.")
        elif evt.state == ConnectionState.DISCONNECTED:
            self.connect_btn.configure(text="Connect")
            self.idn_var.set("")
            self._set_status("Disconnected.")
        elif evt.state == ConnectionState.RECONNECTING:
            self.connect_btn.configure(text="Disconnect")
            self._set_status(f"Connection lost, reconnecting... ({evt.detail})")
        elif evt.state == ConnectionState.CONNECTING:
            self.connect_btn.configure(text="Cancel")

    # -- chart timer --------------------------------------------------------

    def _refresh_chart(self) -> None:
        self.chart_panel.refresh()
        self.root.after(CHART_REFRESH_INTERVAL_MS, self._refresh_chart)

    # -- shutdown -------------------------------------------------------

    def _on_close(self) -> None:
        self.config.host = self.host_var.get().strip()
        self.config.port = self.port_var.get()
        self.config.save()
        self.worker.shutdown(timeout=3.0)
        self.root.destroy()
