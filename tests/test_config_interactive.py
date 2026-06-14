import pytest
from nicegui.testing import User
from nicegui import Client

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
    
    # Find the Speakers number input
    speakers_input = user.find("Speakers")
    
    # Check that it's a number input
    assert speakers_input is not None
    assert len(speakers_input.elements) > 0
    
    # Get the first element from the set
    element = list(speakers_input.elements)[0]
    print(f"Element type: {type(element)}")
    print(f"Element tag: {element.tag}")
    print(f"Element props: {element.props}")
    
    # Check that it's not disabled
    assert not element.props.get("disable", False)
    
    # Try to change the value
    from mimo_acoustic.gui.state import STATE
    old_value = STATE.config["num_speakers"]
    
    # Simulate a value change
    element.value = old_value + 1
    
    # Check that the config was updated
    assert STATE.config["num_speakers"] == old_value + 1

