"""Compatibility entry point; application code lives in src/coc_auto."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from coc_auto.bot import main

if __name__ == '__main__':
    raise SystemExit(main())
