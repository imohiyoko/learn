"""web UI の画面（webui/static/）の ES モジュールのつながりのテスト。

ビルドをしないので、読み込む名前を書き間違えるとブラウザで開くまで気づけない。
ここでは、import している名前が相手のファイルで export されているかだけを確かめる。
"""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webui" / "static"
IMPORT_RE = re.compile(r'^import \{([^}]*)\} from "\./([\w-]+\.js)";', re.M)
EXPORT_RE = re.compile(r"^export (?:async )?(?:function|const|let|class) (\w+|\$\$?)", re.M)


def exports(path):
    return set(EXPORT_RE.findall(path.read_text(encoding="utf-8")))


class StaticModulesTest(unittest.TestCase):
    def test_imports_are_exported(self):
        files = sorted(p for p in STATIC.glob("*.js") if "import {" in p.read_text(encoding="utf-8"))
        self.assertTrue(files)
        for f in files:
            for names, target in IMPORT_RE.findall(f.read_text(encoding="utf-8")):
                with self.subTest(file=f.name, target=target):
                    self.assertTrue((STATIC / target).is_file(), f"{target} がない")
                    missing = {n.strip() for n in names.split(",") if n.strip()} - exports(STATIC / target)
                    self.assertFalse(missing, f"{f.name} が読む {sorted(missing)} を {target} が export していない")

    def test_index_loads_entry_module(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script type="module" src="/main.js"></script>', html)


if __name__ == "__main__":
    unittest.main()
