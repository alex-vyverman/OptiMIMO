"""NiceGUI application entry point for the OptiMIMO room-correction solver."""

from __future__ import annotations

import argparse
from pathlib import Path

from nicegui import ui

from .analysis_tab import AnalysisTab
from .config_tab import ConfigTab
from .measurements_tab import MeasurementsTab
from .run_tab import RunTab
from .state import STATE

CUSTOM_CSS = """
/* ---------- Base ---------- */
body {
    background: #0E111A;
    color: #F5F7FA;
    font-family: 'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

/* ---------- Header ---------- */
.q-header {
    background: #121620 !important;
    border-bottom: 1px solid rgba(0, 229, 255, 0.2) !important;
    backdrop-filter: blur(12px);
    box-shadow: 0 2px 20px rgba(0, 0, 0, 0.3) !important;
}

/* ---------- Tabs ---------- */
.q-tabs__content {
    background: transparent !important;
}
.q-tab {
    color: #7A869A !important;
    border-radius: 8px 8px 0 0 !important;
    transition: all 0.2s ease !important;
    min-height: 44px !important;
    text-transform: none !important;
    font-weight: 500 !important;
    letter-spacing: 0.01em !important;
}
.q-tab:hover {
    color: #F5F7FA !important;
    background: rgba(0, 229, 255, 0.06) !important;
}
.q-tab--active {
    color: #33F0FF !important;
    background: rgba(0, 229, 255, 0.1) !important;
}
.q-tab__indicator {
    color: #00E5FF !important;
    height: 3px !important;
    border-radius: 3px 3px 0 0 !important;
}
.q-tab-panels {
    background: transparent !important;
    padding: 0 !important;
}
.q-tab-panel {
    padding: 24px 16px !important;
}

/* ---------- Cards ---------- */
.q-card {
    background: #1A1F2C !important;
    border: 1px solid rgba(0, 229, 255, 0.08) !important;
    border-radius: 12px !important;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.2) !important;
    padding: 20px !important;
}

/* ---------- Expansion panels ---------- */
.q-expansion-item {
    background: #1A1F2C !important;
    border: 1px solid rgba(0, 229, 255, 0.08) !important;
    border-radius: 10px !important;
    margin-bottom: 8px !important;
    overflow: hidden;
}
.q-expansion-item__container {
    padding: 0 !important;
}
.q-expansion-item .q-item {
    padding: 14px 20px !important;
    min-height: 48px !important;
}
.q-expansion-item .q-item__label {
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    color: #F5F7FA !important;
    letter-spacing: 0.01em !important;
}
.q-expansion-item .q-item__section--side {
    color: #00E5FF !important;
    min-width: 36px !important;
    padding-right: 8px !important;
}
.q-expansion-item__content {
    padding: 4px 20px 16px !important;
}
.q-expansion-item--collapsed {
    border-color: rgba(0, 229, 255, 0.04) !important;
}

/* ---------- Inputs ---------- */
.q-field {
    font-size: 0.85rem !important;
}
.q-field__native, .q-field__input {
    color: #F5F7FA !important;
    padding: 6px 0 !important;
}
.q-field__label {
    color: #7A869A !important;
    font-size: 0.8rem !important;
    font-weight: 500 !important;
}
.q-field--outlined .q-field__control {
    border-color: rgba(0, 229, 255, 0.15) !important;
    border-radius: 8px !important;
    background: rgba(0, 229, 255, 0.03) !important;
    min-height: 40px !important;
}
.q-field--outlined .q-field__control:hover {
    border-color: rgba(0, 229, 255, 0.4) !important;
}
.q-field--outlined.q-field--focused .q-field__control {
    border-color: #00E5FF !important;
    box-shadow: 0 0 0 3px rgba(0, 229, 255, 0.15) !important;
}
.q-field--standard .q-field__control::before {
    border-color: rgba(0, 229, 255, 0.15) !important;
}
.q-field--standard .q-field__control::after {
    border-color: #00E5FF !important;
}
.q-field__marginal {
    color: #7A869A !important;
}

/* Hide number input spinners */
input[type=number]::-webkit-inner-spin-button,
input[type=number]::-webkit-outer-spin-button {
    -webkit-appearance: none;
    margin: 0;
}
input[type=number] {
    -moz-appearance: textfield;
    appearance: textfield;
}

/* ---------- Select / dropdown ---------- */
.q-menu {
    background: #1A1F2C !important;
    border: 1px solid rgba(0, 229, 255, 0.12) !important;
    border-radius: 10px !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4) !important;
}
.q-item {
    color: #F5F7FA !important;
    min-height: 36px !important;
    padding: 6px 16px !important;
}
.q-item:hover, .q-item--active {
    background: rgba(0, 229, 255, 0.12) !important;
    color: #F5F7FA !important;
}

/* ---------- Buttons ---------- */
.q-btn {
    border-radius: 8px !important;
    font-weight: 500 !important;
    text-transform: none !important;
    letter-spacing: 0.01em !important;
    transition: all 0.15s ease !important;
    font-size: 0.85rem !important;
}
.q-btn--flat {
    color: #33F0FF !important;
}
.q-btn--flat:hover {
    background: rgba(0, 229, 255, 0.1) !important;
}
.q-btn:not(.q-btn--flat):not(.q-btn--outline) {
    background: #33F0FF !important;
    color: #0E111A !important;
    box-shadow: 0 2px 8px rgba(0, 229, 255, 0.3) !important;
}
.q-btn:not(.q-btn--flat):not(.q-btn--outline):hover {
    background: #00E5FF !important;
    box-shadow: 0 4px 16px rgba(0, 229, 255, 0.4) !important;
    transform: translateY(-1px);
}
.q-btn:disabled {
    opacity: 0.4 !important;
    transform: none !important;
}

/* ---------- Switch ---------- */
.q-toggle__track {
    background: rgba(255, 255, 255, 0.12) !important;
}
.q-toggle__thumb {
    background: #7A869A !important;
}
.q-toggle--checked .q-toggle__track {
    background: rgba(0, 229, 255, 0.3) !important;
}
.q-toggle--checked .q-toggle__thumb {
    background: #00E5FF !important;
}
.q-toggle__label {
    color: #F5F7FA !important;
    font-size: 0.85rem !important;
}

/* ---------- Checkbox ---------- */
.q-checkbox__bg {
    border-color: rgba(0, 229, 255, 0.2) !important;
}
.q-checkbox--checked .q-checkbox__bg {
    border-color: #00E5FF !important;
    background: #00E5FF !important;
}

/* ---------- Separator ---------- */
.q-separator {
    background: rgba(0, 229, 255, 0.08) !important;
    margin: 12px 0 !important;
}

/* ---------- Tooltip ---------- */
.q-tooltip {
    background: #1A1F2C !important;
    color: #F5F7FA !important;
    border: 1px solid rgba(0, 229, 255, 0.12) !important;
    border-radius: 8px !important;
    padding: 8px 12px !important;
    font-size: 0.78rem !important;
    line-height: 1.5 !important;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4) !important;
}

/* ---------- Progress bar ---------- */
.q-linear-progress__track {
    background: rgba(0, 229, 255, 0.08) !important;
    border-radius: 4px !important;
}
.q-linear-progress__model {
    background: linear-gradient(90deg, #00E5FF, #33F0FF) !important;
    border-radius: 4px !important;
}

/* ---------- Table ---------- */
.q-table {
    background: transparent !important;
    color: #F5F7FA !important;
}
.q-table__card {
    background: #1A1F2C !important;
    border: 1px solid rgba(0, 229, 255, 0.08) !important;
    border-radius: 10px !important;
}
.q-table thead tr th {
    color: #7A869A !important;
    font-weight: 600 !important;
    font-size: 0.75rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
}
.q-table tbody tr {
    border-color: rgba(0, 229, 255, 0.06) !important;
}
.q-table tbody tr:hover {
    background: rgba(0, 229, 255, 0.05) !important;
}

/* ---------- Dialog ---------- */
.q-dialog__inner > div {
    background: #1A1F2C !important;
    border: 1px solid rgba(0, 229, 255, 0.12) !important;
    border-radius: 14px !important;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.5) !important;
}

/* ---------- Notification ---------- */
.q-notification {
    border-radius: 10px !important;
    background: #1A1F2C !important;
    border: 1px solid rgba(0, 229, 255, 0.12) !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4) !important;
}

/* ---------- Scrollbar ---------- */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}
::-webkit-scrollbar-track {
    background: transparent;
}
::-webkit-scrollbar-thumb {
    background: rgba(0, 229, 255, 0.15);
    border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
    background: rgba(0, 229, 255, 0.25);
}

/* ---------- Labels ---------- */
.text-gray-400 { color: #7A869A !important; }
.text-gray-500 { color: #5a6478 !important; }
.text-gray-600 { color: #4a5468 !important; }
.text-positive { color: #34d399 !important; }
.text-negative { color: #f87171 !important; }
.text-orange-600 { color: #fb923c !important; }
.text-xs { font-size: 0.78rem !important; }
.text-sm { font-size: 0.85rem !important; }

/* ---------- Info Icons ---------- */
.q-icon.text-gray-400 {
    color: #00E5FF !important;
    filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.4));
}
"""


@ui.page("/")
def index() -> None:
    analysis = AnalysisTab()
    run = RunTab(analysis)
    measurements = MeasurementsTab()
    config = ConfigTab(on_config_replaced=measurements.refresh)

    ui.page_title("OptiMIMO")
    ui.add_head_html(f"<style>{CUSTOM_CSS}</style>")
    ui.add_head_html(
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
    )

    with ui.header().classes("items-center px-6"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("graphic_eq").classes("text-2xl").style("color: #00E5FF; filter: drop-shadow(0 0 6px rgba(0, 229, 255, 0.5));")
            ui.label("OptiMIMO").classes("text-lg font-semibold tracking-tight")
        ui.space()
        ui.label().bind_text_from(
            STATE,
            "config_path",
            backward=lambda p: str(p.name) if p else "unsaved config",
        ).classes("text-xs opacity-50 font-medium")

    with ui.tabs().classes("w-full px-4").props("dense no-caps align=left") as tabs:
        config_tab = ui.tab("Config", icon="tune")
        measurements_tab = ui.tab("Measurements", icon="mic")
        run_tab = ui.tab("Run", icon="play_circle")
        analysis_tab = ui.tab("Analysis", icon="insights")

    with ui.tab_panels(tabs, value=config_tab).classes("w-full"):
        with ui.tab_panel(config_tab):
            config.build()
        with ui.tab_panel(measurements_tab):
            measurements.build()
        with ui.tab_panel(run_tab):
            run.build()
        with ui.tab_panel(analysis_tab):
            analysis.build()

    def on_disconnect() -> None:
        if run.timer is not None:
            run.timer.cancel()
            run.timer = None

    ui.context.client.on_disconnect(on_disconnect)


def main() -> None:
    parser = argparse.ArgumentParser(description="OptiMIMO room-correction GUI")
    parser.add_argument("--config", type=Path, help="Config JSON to load at startup.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab.")
    args = parser.parse_args()

    if args.config is not None:
        STATE.load_config(args.config.resolve())

    ui.run(
        title="OptiMIMO",
        port=args.port,
        reload=False,
        show=not args.no_browser,
    )


if __name__ == "__main__":
    main()
