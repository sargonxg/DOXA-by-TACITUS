"""Compatibility entry point for the current single-workspace browser suite.

Run .venv/bin/python tests/browser_smoke.py from the repository root.
Historical seven-view checks remain in tests/legacy/browser_smoke_v1.py.
"""

from browser_v3 import main

if __name__ == "__main__":
    main()
