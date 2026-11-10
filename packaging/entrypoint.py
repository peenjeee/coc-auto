"""PyInstaller boot script (worker and dashboard share this executable)."""

from coc_auto.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
