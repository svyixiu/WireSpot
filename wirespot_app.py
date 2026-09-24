"""WireSpot desktop app entry point (PyInstaller builds this into WireSpot.exe)."""
from wirespot.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
