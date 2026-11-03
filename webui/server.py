#!/usr/bin/env python3
"""ブラウザから学習を回すためのローカルサーバーの入口。中身は learn/web/。使い方は --help。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from learn.web.app import main  # noqa: E402

if __name__ == "__main__":
    main()
