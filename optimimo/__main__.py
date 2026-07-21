"""Module entry point so ``python3 -m optimimo ...`` runs the CLI."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
