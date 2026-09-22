"""Small reusable Tk widgets: a validated numeric entry, an LED indicator,
and a labelled value display."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class NumericEntry(ttk.Frame):
    """A labelled numeric entry that commits on Return/focus-out (not per
    keystroke), rejects out-of-range values with a visible error rather
    than silently clamping, and calls `on_commit(value)` only when the
    value is valid and has actually changed."""

    def __init__(
        self,
        parent,
        label: str,
        unit: str,
        minimum: float,
        maximum: float,
        initial: float = 0.0,
        decimals: int = 3,
        on_commit: Callable[[float], None] | None = None,
        width: int = 8,
    ):
        super().__init__(parent)
        self.minimum = minimum
        self.maximum = maximum
        self.decimals = decimals
        self.on_commit = on_commit
        self._value = initial
        self._suppress_commit = False

        ttk.Label(self, text=label).grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=self._fmt(initial))
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, justify="right")
        self.entry.grid(row=0, column=1, padx=(4, 2))
        ttk.Label(self, text=unit).grid(row=0, column=2, sticky="w")

        self.entry.bind("<Return>", self._commit)
        self.entry.bind("<FocusOut>", self._commit)

    def _fmt(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"

    def _commit(self, _event=None) -> None:
        if self._suppress_commit:
            return
        text = self.var.get().strip()
        try:
            value = float(text)
        except ValueError:
            self._flag_error(f"not a number: {text!r}")
            return
        if not (self.minimum <= value <= self.maximum):
            self._flag_error(f"out of range [{self.minimum}, {self.maximum}]: {value}")
            return
        self.entry.configure(foreground="")
        self.var.set(self._fmt(value))
        if value != self._value:
            self._value = value
            if self.on_commit:
                self.on_commit(value)

    def _flag_error(self, _reason: str) -> None:
        self.entry.configure(foreground="red")

    def set_external_value(self, value: float) -> None:
        """Update the displayed value from a device readback, without
        triggering on_commit (this did not come from the user)."""
        self._value = value
        self._suppress_commit = True
        try:
            if self.focus_get() is not self.entry:
                self.var.set(self._fmt(value))
                self.entry.configure(foreground="")
        finally:
            self._suppress_commit = False


class Led(tk.Canvas):
    """A small round status indicator."""

    _COLORS = {
        "off": "#444444",
        "green": "#2ecc71",
        "amber": "#f39c12",
        "red": "#e74c3c",
    }

    def __init__(self, parent, size: int = 14, background: str | None = None):
        super().__init__(parent, width=size, height=size, highlightthickness=0, background=background or "#f0f0f0")
        self._size = size
        self._oval = self.create_oval(1, 1, size - 1, size - 1, fill=self._COLORS["off"], outline="#222222")

    def set_color(self, color: str) -> None:
        self.itemconfigure(self._oval, fill=self._COLORS.get(color, self._COLORS["off"]))

    def set_background(self, color: str) -> None:
        self.configure(background=color)


class ValueDisplay(ttk.Frame):
    """A large read-only value with a unit suffix, for live readbacks."""

    def __init__(self, parent, unit: str, decimals: int = 3, font=("TkDefaultFont", 16, "bold")):
        super().__init__(parent)
        self.decimals = decimals
        self.unit = unit
        self.var = tk.StringVar(value=self._fmt(0.0))
        ttk.Label(self, textvariable=self.var, font=font).pack(side="left")
        ttk.Label(self, text=unit).pack(side="left", padx=(2, 0))

    def _fmt(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"

    def set(self, value: float) -> None:
        self.var.set(self._fmt(value))
