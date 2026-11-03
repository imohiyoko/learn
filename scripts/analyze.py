#!/usr/bin/env python3
"""得意・不得意の分析（/review）。中身は learn/analyze.py。使い方は --help。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from learn.analyze import main  # noqa: E402

if __name__ == "__main__":
    main()
