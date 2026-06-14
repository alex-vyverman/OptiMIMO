"""Minimal server-side file/directory picker dialog for NiceGUI.

NiceGUI has no built-in picker for files on the machine running the app, so
this dialog lists the local filesystem and lets the user navigate and pick a
file or directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from nicegui import ui


class LocalFilePicker(ui.dialog):
    def __init__(
        self,
        directory: Path,
        *,
        title: str = "Select file",
        suffixes: Optional[Sequence[str]] = None,
        pick_directory: bool = False,
    ) -> None:
        super().__init__()
        self.directory = directory if directory.is_dir() else Path.home()
        self.suffixes = tuple(s.lower() for s in suffixes) if suffixes else None
        self.pick_directory = pick_directory

        with self, ui.card().classes("w-[34rem] max-w-full"):
            ui.label(title).classes("text-lg font-medium")
            self.path_label = ui.label(str(self.directory)).classes(
                "text-xs text-gray-500 break-all"
            )
            self.listing = ui.column().classes(
                "w-full h-80 overflow-y-auto border rounded p-1 gap-0"
            )
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: self.submit(None)).props("flat")
                if pick_directory:
                    ui.button(
                        "Select this folder",
                        on_click=lambda: self.submit(self.directory),
                    )
        self._refresh()

    def _refresh(self) -> None:
        self.path_label.set_text(str(self.directory))
        self.listing.clear()
        with self.listing:
            if self.directory.parent != self.directory:
                self._row("\u2B06 ..", self.directory.parent, is_dir=True)
            try:
                children = sorted(
                    self.directory.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except OSError as exc:
                ui.label(f"Cannot read directory: {exc}").classes("text-negative")
                return
            for child in children:
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    self._row(f"\U0001F4C1 {child.name}", child, is_dir=True)
                elif not self.pick_directory:
                    if self.suffixes and child.suffix.lower() not in self.suffixes:
                        continue
                    self._row(child.name, child, is_dir=False)

    def _row(self, label: str, path: Path, *, is_dir: bool) -> None:
        def on_click() -> None:
            if is_dir:
                self.directory = path
                self._refresh()
            else:
                self.submit(path)

        ui.button(label, on_click=on_click).props("flat align=left no-caps").classes(
            "w-full justify-start text-left"
        )


async def pick_file(
    start: Path,
    *,
    title: str = "Select file",
    suffixes: Optional[Sequence[str]] = None,
) -> Optional[Path]:
    return await LocalFilePicker(start, title=title, suffixes=suffixes)


async def pick_directory(start: Path, *, title: str = "Select folder") -> Optional[Path]:
    return await LocalFilePicker(start, title=title, pick_directory=True)
