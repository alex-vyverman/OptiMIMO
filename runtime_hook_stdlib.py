"""Runtime hook to add Python stdlib lib-dynload to sys.path.

This ensures that built-in C extension modules like mmap can be found
when running from a PyInstaller bundle.
"""

import sys
import os

# When running from PyInstaller, add the original Python's lib-dynload to sys.path
if getattr(sys, 'frozen', False):
    # Get the path to the original Python installation
    import sysconfig
    stdlib_path = sysconfig.get_path('stdlib')
    dynload_path = os.path.join(stdlib_path, 'lib-dynload')
    
    if os.path.exists(dynload_path) and dynload_path not in sys.path:
        sys.path.insert(0, dynload_path)
        print(f"[RUNTIME HOOK] Added {dynload_path} to sys.path", file=sys.stderr)
