"""Entry point: python -m psu.app"""

from __future__ import annotations

import tkinter as tk

from psu.config import AppConfig
from psu.ui.main_window import MainWindow


def main() -> None:
    config = AppConfig.load()
    root = tk.Tk()
    MainWindow(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()
