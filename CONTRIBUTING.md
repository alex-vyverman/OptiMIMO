# Contributing to OptiMIMO

Thanks for your interest. This is a small project, so the process is informal.

## Reporting bugs

Open a GitHub issue with: what you ran, what you expected, what happened, and your OS/Python version. If the bug is solver-related, include the relevant parts of your config (with personal paths/names redacted) and a short description of the measurement setup (number of speakers/mics, sample rate, IR length).

## Development setup

```bash
git clone https://github.com/alex-vyverman/OptiMIMO.git
cd OptiMIMO
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"
pip install pytest pytest-asyncio pytest-forked
```

## Running the tests

```bash
pytest tests/
```

GUI tests are process-isolated via `pytest-forked` (configured in [pytest.ini](pytest.ini)).

## Running the GUI in dev mode

```bash
python3 -m optimimo.gui.app
```

Or use the installed entry point:

```bash
mimo-gui
```

Pass `--no-browser` to skip auto-launching the browser, `--port` to change the port.

## Code layout

See the [Code Layout](README.md#code-layout) section in the README.

## Pull requests

- Keep PRs focused on a single change.
- Run `pytest tests/` before opening.
- Match the surrounding code style (no formatter enforced).
- Update the README if you change user-facing behaviour or add a config option.
