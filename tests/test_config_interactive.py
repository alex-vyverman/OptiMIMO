import pytest
from nicegui import Client, ui
from nicegui.testing import User

pytest_plugins = ["nicegui.testing.user_plugin"]

async def test_config_elements_present(user: User):
    """Test that Config tab elements are present and visible."""
    await user.open("/")
    
    # Check that we can see the Config tab elements
    await user.should_see("Dimensions")
    await user.should_see("Speaker profiles")
    await user.should_see("Input routing")
    await user.should_see("Target")
    
    # Check that we can see specific input labels
    await user.should_see("Speakers")
    await user.should_see("Inputs")
    await user.should_see("Mic positions")
    await user.should_see("Sample rate")

async def test_config_number_inputs_interactive(user: User):
    """Test that number inputs on Config tab respond to changes."""
    await user.open("/")
    
    # find(text) selects by visible text. nicegui ignores `kind` when the
    # target is a string, so the match set also includes the tooltip that
    # shares the "Speakers" text. Pick the ui.number explicitly instead of
    # indexing the set, whose iteration order is non-deterministic (it was
    # flaky across platforms/Python builds, failing only when [0] landed on
    # the tooltip).
    speakers_input = user.find("Speakers")
    numbers = [e for e in speakers_input.elements if isinstance(e, ui.number)]
    assert len(numbers) == 1, f"expected one Speakers number input, got {numbers}"
    element = numbers[0]
    assert not element.props.get("disable", False)

    # Drive the change through set_value so the registered on_change handler
    # fires, then yield to the event loop so its side effects land before we
    # assert on STATE.
    from optimimo.gui.state import STATE
    old_value = STATE.config["num_speakers"]
    element.set_value(old_value + 1)
    await user.should_see("Speakers")

    assert STATE.config["num_speakers"] == old_value + 1

