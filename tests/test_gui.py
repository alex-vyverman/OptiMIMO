"""GUI smoke tests using NiceGUI's simulated-user test framework.

These build the real pages server-side, so exceptions anywhere in the tab
builders fail the tests. The end-to-end test runs an actual solve on
synthetic measurements through the Run tab.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
from nicegui.testing import User
from scipy.io import wavfile

from mimo_acoustic.cli import synthetic_room_irs
from mimo_acoustic.gui.state import STATE

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(autouse=True)
def fresh_state() -> None:
    STATE.new_from_example()
    STATE.result = None
    STATE.export_paths = None
    STATE.running = False
    STATE.last_error = ""


async def test_index_builds(user: User) -> None:
    await user.open("/")
    await user.should_see("MIMO Room Correction")
    await user.should_see("Speaker profiles")
    await user.should_see("Input routing")
    await user.should_see("Target")


async def test_reload_does_not_touch_deleted_clients(user: User, caplog) -> None:
    """Regression test for NiceGUI issue #3028: module-level refreshables used
    to accumulate targets across clients, so any refresh after a browser
    reload warned 'Client has been deleted but is still being used'."""
    await user.open("/")  # first client
    await user.open("/")  # simulated reload -> second client
    user.find("New from example").click()  # triggers refresh_all()
    user.find("Validate config").click()  # triggers result refresh
    await user.should_see("Config is valid.")
    await asyncio.sleep(0.5)
    assert not any(
        "Client has been deleted" in record.getMessage() for record in caplog.records
    ), "refresh touched a deleted client"


async def test_validate_example_config(user: User) -> None:
    await user.open("/")
    user.find("Validate config").click()
    await user.should_see("Config is valid.")


async def test_validate_reports_issues(user: User) -> None:
    # Use problems the GUI cannot auto-repair: an inverted speaker band and a
    # bogus output format. (A missing input_primary_speaker is auto-created by
    # the target-mode select on page build, so it cannot be tested this way.)
    STATE.config["speaker_profiles"]["0"]["max_hz"] = 5.0  # below min_hz
    STATE.config["output_format"] = "bogus"
    await user.open("/")
    user.find("Validate config").click()
    await user.should_see("speaker_profiles")
    await user.should_see("output_format")


async def test_full_solve_through_gui(user: User, tmp_path: Path) -> None:
    sample_rate = 48000
    num_mics = 2
    num_speakers = 2
    room = synthetic_room_irs(sample_rate, num_mics, num_speakers, length=1024)
    measurements = []
    for mic in range(num_mics):
        for speaker in range(num_speakers):
            path = tmp_path / f"ir_m{mic}_s{speaker}.wav"
            wavfile.write(path, sample_rate, room[mic, speaker].astype(np.float32))
            measurements.append({"speaker": speaker, "mic": mic, "path": str(path)})

    STATE.config = {
        "num_speakers": num_speakers,
        "num_mic_positions": num_mics,
        "num_inputs": num_speakers,
        "sample_rate": sample_rate,
        "filter_taps": 1024,
        "target_delay_ms": 10.0,
        "output_dir": str(tmp_path / "out"),
        "output_format": "both",
        "speaker_profiles": {
            "0": {"name": "L", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
            "1": {"name": "R", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
        },
        "measurements": measurements,
    }
    STATE.normalize_config()

    await user.open("/")
    user.find("Run solver").click()
    for _ in range(300):
        if STATE.result is not None or STATE.last_error:
            break
        await asyncio.sleep(0.1)
    assert STATE.last_error == "", f"solve failed: {STATE.last_error}"
    assert STATE.result is not None
    assert STATE.result.firs.shape == (1024, 2, 2)

    user.find("Export FIRs + YAML").click()
    for _ in range(100):
        if STATE.export_paths is not None:
            break
        await asyncio.sleep(0.1)
    assert STATE.export_paths is not None
    assert STATE.export_paths.yaml_path.exists()
    assert len(STATE.export_paths.filter_paths) == 4

    # Analysis tab should now render plots and the residual table.
    user.find("Refresh plots").click()
    await user.should_see("Residual error")
    await user.should_see("Impulse envelope")
