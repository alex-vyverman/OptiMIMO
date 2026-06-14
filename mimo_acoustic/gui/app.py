"""NiceGUI application entry point for the MIMO room-correction solver."""

from __future__ import annotations

import argparse
from pathlib import Path

from nicegui import ui

from .analysis_tab import AnalysisTab
from .config_tab import ConfigTab
from .measurements_tab import MeasurementsTab
from .run_tab import RunTab
from .state import STATE


@ui.page("/")
def index() -> None:
    # One instance of each tab per client, so refreshable targets never
    # outlive their client (NiceGUI issue #3028).
    analysis = AnalysisTab()
    run = RunTab(analysis)
    measurements = MeasurementsTab()
    config = ConfigTab(on_config_replaced=measurements.refresh)

    ui.page_title("MIMO Room Correction")
    with ui.header().classes("items-center"):
        ui.label("MIMO Room Correction").classes("text-lg font-medium")
        ui.space()
        ui.label().bind_text_from(
            STATE,
            "config_path",
            backward=lambda p: str(p) if p else "unsaved config",
        ).classes("text-xs opacity-70")

    with ui.tabs().classes("w-full") as tabs:
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

    def on_disconnect() -> None:
        if run.timer is not None:
            run.timer.cancel()
            run.timer = None

    ui.context.client.on_disconnect(on_disconnect)

    def on_disconnect() -> None:
        if run.timer is not None:
            run.timer.cancel()
            run.timer = None

    ui.context.client.on_disconnect(on_disconnect)

    def on_disconnect() -> None:
        if run.timer is not None:
            run.timer.cancel()
            run.timer = None

    ui.context.client.on_disconnect(on_disconnect)

    def on_disconnect() -> None:
        if run.timer is not None:
            run.timer.cancel()
            run.timer = None

    ui.context.client.on_disconnect(on_disconnect)


def main() -> None:
    parser = argparse.ArgumentParser(description="MIMO room-correction GUI")
    parser.add_argument("--config", type=Path, help="Config JSON to load at startup.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab.")
    args = parser.parse_args()

    if args.config is not None:
        STATE.load_config(args.config.resolve())

    ui.run(
        title="MIMO Room Correction",
        port=args.port,
        reload=False,
        show=not args.no_browser,
    )


if __name__ == "__main__":
    main()
