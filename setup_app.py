"""WireSpot installer entry point (PyInstaller builds this into WireSpotSetup.exe,
with WireSpot.exe and WireSpotCLI.exe embedded as its payload)."""
from wirespot.setup import main

if __name__ == "__main__":
    raise SystemExit(main())
