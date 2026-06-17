"""OptiMIMO launcher - entry point for PyInstaller binary builds.

This script uses absolute imports so PyInstaller can bundle it correctly.
"""

import sys
import os

# Ensure the bundled package is on the path
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

if base_path not in sys.path:
    sys.path.insert(0, base_path)

from optimimo.gui.app import main

if __name__ == "__main__":
    main()
