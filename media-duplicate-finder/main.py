#!/usr/bin/env python3
"""
Media Duplicate Finder – entry point.

Launch the tkinter GUI application.  Run with:

    python3 main.py

Prerequisites:
    • Python 3.8+
    • python3-tk  (sudo apt install python3-tk)
    • ffmpeg / ffprobe  (sudo apt install ffmpeg)  – optional but recommended
    • Pillow  (pip install Pillow)  – optional, for perceptual image hashing
"""

import sys
import os

# Ensure the project root is on sys.path so that imports work when run from
# any working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    # Quick pre-flight check for tkinter
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(
            "ERROR: tkinter is not available.\n"
            "Install it with:  sudo apt install python3-tk",
            file=sys.stderr,
        )
        sys.exit(1)

    from gui.app import App

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
