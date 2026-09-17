#!/usr/bin/env python3
"""Convenience launcher — run the app with `python run.py` from the project root.

Equivalent to `python -m pro_video_suite`, but works whether or not the package
has been pip-installed, by making sure `src/` is importable first.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pro_video_suite.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
