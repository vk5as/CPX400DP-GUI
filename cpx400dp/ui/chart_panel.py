"""Live matplotlib chart of voltage/current history for both channels.

Two stacked axes (volts on top, amps below) sharing the x-axis, redrawn on
its own timer decoupled from the data pump so a slow chart can't stall
event draining. Uses blitting for the common case (new data, same axis
limits) and falls back to a full draw when limits actually change.
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from cpx400dp.history import History  # noqa: E402
from cpx400dp.ui.themes import LIGHT, Palette  # noqa: E402

WINDOWS = {
    "10 s": 10.0,
    "30 s": 30.0,
    "1 min": 60.0,
    "5 min": 300.0,
    "30 min": 1800.0,
    "All": None,
}


class ChartPanel(ttk.LabelFrame):
    def __init__(self, parent, history: History):
        super().__init__(parent, text="Live chart", padding=8)
        self.history = history
        self.paused = False
        self._t0 = time.monotonic()
        self._palette = LIGHT

        controls = ttk.Frame(self)
        controls.pack(side="top", fill="x")
        ttk.Label(controls, text="Window").pack(side="left")
        self.window_var = tk.StringVar(value="1 min")
        window_combo = ttk.Combobox(
            controls, textvariable=self.window_var, values=list(WINDOWS.keys()), width=8, state="readonly"
        )
        window_combo.pack(side="left", padx=4)
        window_combo.bind("<<ComboboxSelected>>", lambda _e: self._full_redraw())

        self.pause_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Pause", variable=self.pause_var, command=self._on_pause_toggled).pack(
            side="left", padx=8
        )
        self.autoscale_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Autoscale", variable=self.autoscale_var, command=self._full_redraw).pack(
            side="left", padx=4
        )
        ttk.Button(controls, text="Clear", command=self._on_clear).pack(side="left", padx=4)
        ttk.Button(controls, text="Export CSV", command=self._on_export).pack(side="left", padx=4)

        self.figure = Figure(figsize=(7, 3.2), dpi=100)
        self.ax_v = self.figure.add_subplot(2, 1, 1)
        self.ax_i = self.figure.add_subplot(2, 1, 2, sharex=self.ax_v)
        self.ax_v.set_ylabel("Volts")
        self.ax_i.set_ylabel("Amps")
        self.ax_i.set_xlabel("seconds ago")
        self.figure.tight_layout()

        (self.line_v1,) = self.ax_v.plot([], [], color="#2980b9", label="V1")
        (self.line_v2,) = self.ax_v.plot([], [], color="#c0392b", label="V2")
        (self.line_i1,) = self.ax_i.plot([], [], color="#2980b9", label="I1")
        (self.line_i2,) = self.ax_i.plot([], [], color="#c0392b", label="I2")
        self.ax_v.legend(loc="upper left", fontsize=8)
        self.ax_i.legend(loc="upper left", fontsize=8)

        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        self.canvas.draw()
        self._background = None
        self._capture_background()

    def apply_theme(self, palette: Palette) -> None:
        self._palette = palette
        self.figure.set_facecolor(palette.surface)
        for ax in (self.ax_v, self.ax_i):
            ax.set_facecolor(palette.surface)
            ax.tick_params(colors=palette.foreground)
            ax.xaxis.label.set_color(palette.foreground)
            ax.yaxis.label.set_color(palette.foreground)
            for spine in ax.spines.values():
                spine.set_color(palette.border)
            legend = ax.get_legend()
            if legend is not None:
                legend.get_frame().set_facecolor(palette.surface)
                legend.get_frame().set_edgecolor(palette.border)
                for text in legend.get_texts():
                    text.set_color(palette.foreground)
        self._full_redraw()

    def _on_pause_toggled(self) -> None:
        self.paused = self.pause_var.get()

    def _on_clear(self) -> None:
        self.history.clear()
        self._full_redraw()

    def _on_export(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="cpx400dp_history.csv",
        )
        if not path:
            return
        count = self.history.export_csv(Path(path))
        messagebox.showinfo("Export complete", f"Wrote {count} rows to {path}")

    def _capture_background(self) -> None:
        self.canvas.draw()
        self._background = self.canvas.copy_from_bbox(self.figure.bbox)

    def refresh(self) -> None:
        """Called periodically (e.g. every 200ms) from the main window's
        timer. Cheap when paused."""
        if self.paused:
            return
        window_s = WINDOWS[self.window_var.get()]
        samples = self.history.since(window_s)
        samples = History.decimate(samples, max_points=2000)
        if not samples:
            return

        now = samples[-1].t
        xs = [s.t - now for s in samples]  # seconds ago, <= 0
        v1 = [s.v1 for s in samples]
        v2 = [s.v2 for s in samples]
        i1 = [s.i1 for s in samples]
        i2 = [s.i2 for s in samples]

        self.line_v1.set_data(xs, v1)
        self.line_v2.set_data(xs, v2)
        self.line_i1.set_data(xs, i1)
        self.line_i2.set_data(xs, i2)

        if self.autoscale_var.get():
            self._full_redraw()
            return

        try:
            self.canvas.restore_region(self._background)
            self.ax_v.draw_artist(self.line_v1)
            self.ax_v.draw_artist(self.line_v2)
            self.ax_i.draw_artist(self.line_i1)
            self.ax_i.draw_artist(self.line_i2)
            self.canvas.blit(self.figure.bbox)
        except Exception:
            self._full_redraw()

    def _full_redraw(self) -> None:
        window_s = WINDOWS[self.window_var.get()]
        samples = History.decimate(self.history.since(window_s), max_points=2000)
        if samples:
            now = samples[-1].t
            xs = [s.t - now for s in samples]
            self.line_v1.set_data(xs, [s.v1 for s in samples])
            self.line_v2.set_data(xs, [s.v2 for s in samples])
            self.line_i1.set_data(xs, [s.i1 for s in samples])
            self.line_i2.set_data(xs, [s.i2 for s in samples])
            self.ax_v.set_xlim(min(xs), max(xs) if max(xs) > min(xs) else min(xs) + 1)
        self.ax_v.relim()
        self.ax_v.autoscale_view(scalex=False, scaley=True)
        self.ax_i.relim()
        self.ax_i.autoscale_view(scalex=False, scaley=True)
        self.canvas.draw()
        self._capture_background()
