"""Main file for NiceGUI's simulated-user test framework.

The test plugin re-executes this file for every test (after resetting
NiceGUI's globals), so the page must be (re-)registered here rather than
relying on import-time decorators in mimo_acoustic.gui.app.
"""

from nicegui import ui

from mimo_acoustic.gui import app

ui.page("/")(app.index)
ui.run(storage_secret="test")
