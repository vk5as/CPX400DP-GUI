"""CI smoke test for the release workflow.

Run after `pip install`-ing the built wheel on a real Windows runner (the
only environment where opening a live Tk window is safe in CI without a
virtual display). Verifies the whole GUI actually constructs -- every
panel, theming, and the worker thread's startup/shutdown path -- not just
that the modules import.
"""

from __future__ import annotations

import sys
import tkinter as tk

from cpx400dp.config import AppConfig
from cpx400dp.ui.main_window import MainWindow


def main() -> None:
    root = tk.Tk()
    try:
        win = MainWindow(root, AppConfig())
        root.update()
        win._on_close()  # exercises the real shutdown path (worker.shutdown(), config.save())
    except Exception:
        root.destroy()
        raise

    print(f"GUI construction OK on Python {sys.version}")


if __name__ == "__main__":
    main()
