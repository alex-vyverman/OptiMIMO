"""Checks for the conditional Config tab fields and decluttered chrome."""
import pytest
from nicegui import ui
from nicegui.testing import User

from optimimo.gui.state import STATE

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(autouse=True)
def fresh_state() -> None:
    STATE.new_from_template()
    STATE.result = None


async def test_new_loads_minimal_template(user: User) -> None:
    await user.open("/")
    await user.should_see("Sub")  # minimal template profile names
    await user.should_see("Main L")
    await user.should_see("Advanced")
    assert STATE.config["num_speakers"] == 3
    assert STATE.config["target_mode"] == "anchored"


async def test_max_cut_hidden_until_floor_enabled(user: User) -> None:
    await user.open("/")
    await user.should_not_see("Max cut dB")
    toggle = next(e for e in user.find("Diagonal cut floor").elements if isinstance(e, ui.switch))
    toggle.set_value(True)
    await user.should_see("Max cut dB")


async def test_target_level_field_appears_when_auto_off(user: User) -> None:
    await user.open("/")
    await user.should_not_see("Target level (linear)")
    toggle = next(e for e in user.find("Auto target level").elements if isinstance(e, ui.switch))
    toggle.set_value(False)
    await user.should_see("Target level (linear)")
    assert "target_level_linear" in STATE.config
    toggle = next(e for e in user.find("Auto target level").elements if isinstance(e, ui.switch))
    toggle.set_value(True)
    assert "target_level_linear" not in STATE.config


async def test_debug_buttons_gone(user: User) -> None:
    await user.open("/")
    await user.should_not_see("Force Refresh")
    await user.should_not_see("Show Config")
    await user.should_not_see("Profile disable threshold")
