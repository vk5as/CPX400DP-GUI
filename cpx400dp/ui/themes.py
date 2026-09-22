"""Cross-platform theming for the Tk UI.

Tk's platform-native ttk themes ('vista' on Windows, 'aqua' on macOS)
can't be recolored — they ignore most style options by design, since they
delegate drawing to the OS. A real Light/Dark palette is only possible on
top of 'clam', a pure-Tcl theme that renders identically (and is always
available) on Windows, macOS, and Linux alike.

Light and Dark are registered as their own named themes ("cpx400dp_light",
"cpx400dp_dark") cloned from 'clam' via style.theme_create, rather than mutating
'clam' in place. That distinction matters: 'clam' is also one of the
fallback candidates for "System" on platforms with no native ttk theme
(Linux, mainly) — if Light/Dark repainted 'clam' directly, switching to
"System" there would inherit whatever colors were last applied instead of
looking like a neutral, undecorated Tk app.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True)
class Palette:
    name: str
    background: str
    surface: str
    foreground: str
    accent: str
    field_bg: str
    trough: str
    select_bg: str
    select_fg: str
    border: str
    warning: str


LIGHT = Palette(
    name="Light",
    background="#f0f0f0",
    surface="#ffffff",
    foreground="#1a1a1a",
    accent="#2563eb",
    field_bg="#ffffff",
    trough="#d9d9d9",
    select_bg="#2563eb",
    select_fg="#ffffff",
    border="#b0b0b0",
    warning="#c0392b",
)

DARK = Palette(
    name="Dark",
    background="#232629",
    surface="#2b2f33",
    foreground="#e6e6e6",
    accent="#4d9dff",
    field_bg="#33373b",
    trough="#3a3f44",
    select_bg="#4d9dff",
    select_fg="#0d0d0d",
    border="#4b5157",
    warning="#ff6b5b",
)

PALETTES: dict[str, Palette] = {"Light": LIGHT, "Dark": DARK}
SYSTEM = "System"

THEME_NAMES = [*PALETTES.keys(), SYSTEM]

# Native ttk themes to try, in preference order, for "System". Only the
# ones matching the current OS actually show up in style.theme_names().
# 'clam'/'default' are pure-Tcl and always present, so they're the final
# fallback on platforms (mainly Linux) with no OS-native ttk theme.
_SYSTEM_PREFERENCE = ["vista", "xpnative", "winnative", "aqua", "clam", "default"]


def get_palette(name: str) -> Palette | None:
    """Return the Palette for a named theme, or None for 'System' (which
    has no fixed palette — it defers to the OS's native colors)."""
    return PALETTES.get(name)


def apply_theme(root: tk.Tk, name: str) -> None:
    style = ttk.Style(root)
    if name == SYSTEM:
        _apply_system(style, root)
        return
    _apply_palette(style, root, PALETTES.get(name, LIGHT))


def _apply_system(style: ttk.Style, root: tk.Tk) -> None:
    available = style.theme_names()
    for theme in _SYSTEM_PREFERENCE:
        if theme in available:
            style.theme_use(theme)
            break
    bg = style.lookup("TFrame", "background") or "#f0f0f0"
    root.configure(background=bg)


def _apply_palette(style: ttk.Style, root: tk.Tk, p: Palette) -> None:
    theme_name = f"psu_{p.name.lower()}"
    settings = _build_settings(p)
    if theme_name in style.theme_names():
        style.theme_settings(theme_name, settings)
    else:
        style.theme_create(theme_name, parent="clam", settings=settings)
    style.theme_use(theme_name)
    root.configure(background=p.background)


def _build_settings(p: Palette) -> dict:
    return {
        ".": {
            "configure": {
                "background": p.background,
                "foreground": p.foreground,
                "fieldbackground": p.field_bg,
                "bordercolor": p.border,
                "lightcolor": p.background,
                "darkcolor": p.background,
                "troughcolor": p.trough,
                "arrowcolor": p.foreground,
            }
        },
        "TFrame": {"configure": {"background": p.background}},
        "TLabelframe": {
            "configure": {
                "background": p.background,
                "foreground": p.foreground,
                "bordercolor": p.border,
            }
        },
        "TLabelframe.Label": {"configure": {"background": p.background, "foreground": p.foreground}},
        "TLabel": {"configure": {"background": p.background, "foreground": p.foreground}},
        "TButton": {
            "configure": {"background": p.surface, "foreground": p.foreground, "bordercolor": p.border},
            "map": {
                "background": [("pressed", p.accent), ("active", p.accent)],
                "foreground": [("pressed", p.select_fg), ("active", p.select_fg)],
            },
        },
        "Toolbutton": {
            "configure": {"background": p.surface, "foreground": p.foreground, "bordercolor": p.border},
            "map": {
                "background": [("selected", p.accent), ("active", p.accent)],
                "foreground": [("selected", p.select_fg), ("active", p.select_fg)],
            },
        },
        "TCheckbutton": {
            "configure": {"background": p.background, "foreground": p.foreground},
            "map": {"background": [("active", p.background)], "foreground": [("active", p.foreground)]},
        },
        "TRadiobutton": {
            "configure": {"background": p.background, "foreground": p.foreground},
            "map": {"background": [("active", p.background)], "foreground": [("active", p.foreground)]},
        },
        "TEntry": {
            "configure": {
                "fieldbackground": p.field_bg,
                "foreground": p.foreground,
                "bordercolor": p.border,
                "insertcolor": p.foreground,
            }
        },
        "TCombobox": {
            "configure": {
                "fieldbackground": p.field_bg,
                "foreground": p.foreground,
                "background": p.surface,
                "arrowcolor": p.foreground,
            },
            "map": {
                "fieldbackground": [("readonly", p.field_bg)],
                "foreground": [("readonly", p.foreground)],
            },
        },
        "TSpinbox": {
            "configure": {
                "fieldbackground": p.field_bg,
                "foreground": p.foreground,
                "background": p.surface,
                "bordercolor": p.border,
                "arrowcolor": p.foreground,
            }
        },
        "TNotebook": {"configure": {"background": p.background, "bordercolor": p.border}},
        "TNotebook.Tab": {
            "configure": {"background": p.surface, "foreground": p.foreground},
            "map": {
                "background": [("selected", p.accent)],
                "foreground": [("selected", p.select_fg)],
            },
        },
        "Horizontal.TScale": {"configure": {"background": p.background, "troughcolor": p.trough}},
        "TScrollbar": {
            "configure": {
                "background": p.surface,
                "troughcolor": p.trough,
                "bordercolor": p.border,
                "arrowcolor": p.foreground,
            }
        },
    }
