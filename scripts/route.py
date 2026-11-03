#!/usr/bin/env python3
"""問題バンクから次に解く問題を選ぶ（/question・/drill・/prepare）。中身は learn/route.py。使い方は --help。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from learn.route import main  # noqa: E402

if __name__ == "__main__":
    main()
