"""WireSpot engine entry point (PyInstaller builds this into WireSpotEngine.exe,
which the desktop app, WireSpot.exe, starts and talks to; see wirespot/bridge.py)."""
from wirespot.bridge import main

if __name__ == "__main__":
    raise SystemExit(main())
