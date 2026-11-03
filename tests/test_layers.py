"""learn/ の依存の向きのテスト：webui → stats・analyze・route → model・core。

逆向きに読むと、部品を直したときの影響が読めなくなるので、ここで止める。
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# モジュール → 読んでよい learn のモジュール
ALLOWED = {
    "core": set(),
    "model": set(),
    "stats": {"core"},
    "analyze": {"core", "model"},
    "route": {"core", "model"},
}


def learn_imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "learn":
            parts = node.module.split(".")
            out |= {parts[1]} if len(parts) > 1 else {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {a.name.split(".")[1] for a in node.names if a.name.startswith("learn.")}
            if any(a.name in ("webui", "server") for a in node.names):
                out.add("webui")
    return out


class LayersTest(unittest.TestCase):
    def test_every_module_listed(self):
        mods = {p.stem for p in (ROOT / "learn").glob("*.py") if p.stem != "__init__"}
        self.assertEqual(mods, set(ALLOWED), "learn/ にモジュールを足したら ALLOWED にも足す")

    def test_dependency_direction(self):
        for name, allowed in ALLOWED.items():
            with self.subTest(name):
                got = learn_imports(ROOT / "learn" / f"{name}.py")
                self.assertTrue(got <= allowed, f"learn/{name}.py が {sorted(got - allowed)} を読んでいる")


if __name__ == "__main__":
    unittest.main()
