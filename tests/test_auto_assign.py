"""Unit tests for the auto-assign helper used by the measurements dialogs.

Tests the pure matching logic — no GUI involvement.
"""

from optimimo.gui.measurements_tab import _auto_assign


def _cells_for(speakers, mics):
    """Build cells in (mic, speaker, mic_name, speaker_name) order."""
    return [
        (m_idx, s_idx, m_name, s_name)
        for s_idx, s_name in enumerate(speakers)
        for m_idx, m_name in enumerate(mics)
    ]


def test_unique_filename_per_cell_assigns_all():
    speakers = ["Sub L", "Front L", "Front R"]
    mics = ["MLP", "Left"]
    cells = _cells_for(speakers, mics)
    files = [
        "Sub L_MLP.wav", "Sub L_Left.wav",
        "Front L_MLP.wav", "Front L_Left.wav",
        "Front R_MLP.wav", "Front R_Left.wav",
    ]
    candidates = [(f, f) for f in files]

    result = _auto_assign(candidates, cells)

    assert result[(0, 0)] == "Sub L_MLP.wav"
    assert result[(1, 0)] == "Sub L_Left.wav"
    assert result[(0, 1)] == "Front L_MLP.wav"
    assert result[(1, 1)] == "Front L_Left.wav"
    assert result[(0, 2)] == "Front R_MLP.wav"
    assert result[(1, 2)] == "Front R_Left.wav"


def test_case_insensitive_matching():
    cells = _cells_for(speakers=["Sub L"], mics=["MLP"])
    candidates = [("sub_l_mlp.WAV", "sub_l_mlp.WAV")]
    assert _auto_assign(candidates, cells) == {(0, 0): "sub_l_mlp.WAV"}


def test_no_match_yields_no_assignment():
    cells = _cells_for(speakers=["Sub L"], mics=["MLP"])
    candidates = [("random.wav", "random.wav")]
    assert _auto_assign(candidates, cells) == {}


def test_greedy_prefers_distinctive_cells():
    """The ambiguous 'L_MLP' file matches both 'L' and 'Sub L' speakers; the
    distinctive 'Sub L_MLP' only matches 'Sub L'. The distinctive cell must
    win its only candidate so 'L' still has a match left ('L_MLP')."""
    speakers = ["L", "Sub L"]
    mics = ["MLP"]
    cells = _cells_for(speakers, mics)
    files = ["L_MLP.wav", "Sub L_MLP.wav"]
    candidates = [(f, f) for f in files]

    result = _auto_assign(candidates, cells)

    assert result[(0, 1)] == "Sub L_MLP.wav"  # the distinctive cell
    assert result[(0, 0)] == "L_MLP.wav"  # leftover for the ambiguous cell


def test_each_candidate_assigned_at_most_once():
    speakers = ["L", "Sub L"]
    mics = ["MLP"]
    cells = _cells_for(speakers, mics)
    # Only one file; can only cover one cell.
    candidates = [("Sub L_MLP.wav", "Sub L_MLP.wav")]

    result = _auto_assign(candidates, cells)

    values = list(result.values())
    assert len(values) == 1
    assert values[0] == "Sub L_MLP.wav"


def test_empty_names_are_skipped():
    cells = [(0, 0, "", "Sub L"), (0, 1, "MLP", "")]
    candidates = [("Sub L_MLP.wav", "Sub L_MLP.wav")]
    assert _auto_assign(candidates, cells) == {}


def test_rew_style_uuid_and_title_split():
    """Verify the value/label split: REW uses uuid as value, title as label."""
    cells = _cells_for(speakers=["Sub L"], mics=["MLP"])
    candidates = [("uuid-1234", "Sub L sweep at MLP"), ("uuid-5678", "unrelated")]

    result = _auto_assign(candidates, cells)

    assert result == {(0, 0): "uuid-1234"}
