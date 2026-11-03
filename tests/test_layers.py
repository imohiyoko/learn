"""learn/ の依存の向きのテスト：web → stats・analyze・route → model・core。

web（learn/web/）の中は app → home・jobs・sets・refs → glossary → files。

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

# learn/web/ の中の向き（app だけが全部を読む）
WEB_ALLOWED = {
    "files": set(),
    "glossary": {"files"},
    "sets": {"files", "glossary"},
    "refs": {"files"},
    "jobs": {"files", "sets"},
    "home": {"files", "jobs", "sets"},
    "app": {"files", "glossary", "home", "jobs", "refs", "sets"},
}


def web_imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "learn.web":
                out |= {a.name for a in node.names}
            elif node.module.startswith("learn.web."):
                out.add(node.module.split(".")[2])
    return out


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

    def test_core_modules_do_not_use_web(self):
        for name in ALLOWED:
            with self.subTest(name):
                self.assertNotIn("web", learn_imports(ROOT / "learn" / f"{name}.py"))

    def test_web_direction(self):
        web = ROOT / "learn" / "web"
        mods = {p.stem for p in web.glob("*.py") if p.stem != "__init__"}
        self.assertEqual(mods, set(WEB_ALLOWED), "learn/web/ にモジュールを足したら WEB_ALLOWED にも足す")
        for name, allowed in WEB_ALLOWED.items():
            with self.subTest(name):
                got = web_imports(web / f"{name}.py")
                self.assertTrue(got <= allowed, f"learn/web/{name}.py が {sorted(got - allowed)} を読んでいる")


if __name__ == "__main__":
    unittest.main()
