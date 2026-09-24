"""WireSpot entry point (PyInstaller builds this file into WireSpot.exe)."""
from wirespot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
