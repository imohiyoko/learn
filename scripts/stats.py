#!/usr/bin/env python3
"""成績の集計と、出題分野の簡単な推薦。中身は learn/stats.py。使い方は --help。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from learn.stats import main  # noqa: E402

if __name__ == "__main__":
    main()
