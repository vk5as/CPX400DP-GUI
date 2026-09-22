"""Entry point: python -m cpx400dp.app"""

from __future__ import annotations

import tkinter as tk

from cpx400dp.config import AppConfig
from cpx400dp.ui.main_window import MainWindow


def main() -> None:
    config = AppConfig.load()
    root = tk.Tk()
    MainWindow(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()
