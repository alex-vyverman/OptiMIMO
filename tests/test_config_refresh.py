"""Regression tests for the Config tab refresh wiring.

Previously a duplicated ``_refresh_dependent_sections`` shadowed the correct
one, so changing the speaker/input/mic counts never rebuilt the dependent
sections and the user had to click "Force Refresh" to see the change. These
tests pin the refresh contract without needing a live UI context.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from optimimo.gui.config_tab import ConfigTab

ALL_SECTIONS = [
    "_file_bar",
    "_dimensions_section",
    "_profiles_section",
    "_routing_section",
    "_target_section",
    "_filter_section",
    "_smoothing_section",
    "_output_section",
]


def _tab_with_mocked_sections() -> ConfigTab:
    tab = ConfigTab()
    for name in ALL_SECTIONS:
        setattr(tab, name, MagicMock())
    return tab


def test_refresh_all_rebuilds_every_section():
    tab = _tab_with_mocked_sections()
    tab.refresh_all()
    for name in ALL_SECTIONS:
        getattr(tab, name).refresh.assert_called_once()


def test_refresh_dependent_sections_rebuilds_dimension_dependents():
    tab = _tab_with_mocked_sections()
    tab._refresh_dependent_sections()
    # The sections whose contents depend on the dimensions must rebuild...
    tab._profiles_section.refresh.assert_called_once()
    tab._routing_section.refresh.assert_called_once()
    tab._target_section.refresh.assert_called_once()
    # ...but not the Dimensions section itself, so the edited number field
    # keeps focus.
    tab._dimensions_section.refresh.assert_not_called()


def test_dependent_refresh_notifies_measurements():
    tab = ConfigTab(on_config_replaced=MagicMock())
    for name in ALL_SECTIONS:
        setattr(tab, name, MagicMock())
    tab._refresh_dependent_sections()
    tab.on_config_replaced.assert_called_once()
