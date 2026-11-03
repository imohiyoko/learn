"""穴埋め（数字・語句で答える問題）の読み取りと採点。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from learn import fill, route  # noqa: E402
from test_route import make_bank  # noqa: E402

ITEM = """---
id: b-web-c0-f0
kind: fill
tags: [web]
concepts: [web/c0]
difficulty: 2
verified: true
---
商品 50,000円を掛けで仕入れた。

| 借方科目 | 金額 | 貸方科目 | 金額 |
|---|---|---|---|
| ［ア］ | ［イ］ | ［ウ］ | ［イ］ |
"""

KEY = """---
id: b-web-c0-f0
---
## 正解

- ア：仕入
- イ：50,000
- ウ：買掛金｜買掛

## 解説

- エ：ここは読まない
"""


def add_fill(root):
    (root / "bank" / "items" / "b-web-c0-f0.md").write_text(ITEM, encoding="utf-8")
    (root / "bank" / "keys" / "b-web-c0-f0.md").write_text(KEY, encoding="utf-8")


class FillTest(unittest.TestCase):
    def test_blanks_in_order_once(self):
        self.assertEqual(fill.blanks(ITEM), ["ア", "イ", "ウ"])

    def test_key_answers_stop_at_explanation(self):
        self.assertEqual(fill.key_answers(KEY), {"ア": ["仕入"], "イ": ["50,000"], "ウ": ["買掛金", "買掛"]})

    def test_normalize_numbers_and_words(self):
        same = [("50,000", "５００００円"), ("△1,000", "-1000"), ("▲1000", "-1,000"), ("1.50", "1.5"),
                ("買掛 金", "買掛金"), ("ＤＮＳ", "dns"), ("1,000.0", "1000"), ("0050", "50"), ("-0", "0"), ("0.50", ".5")]
        for a, b in same:
            with self.subTest(a=a, b=b):
                self.assertEqual(fill.normalize(a), fill.normalize(b))
        for a, b in (("50,000", "5,000"), ("買掛金", "売掛金"), ("1000", "-1000"),
                     ("9007199254740992", "9007199254740993")):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(fill.normalize(a), fill.normalize(b))

    def test_huge_number_does_not_crash(self):
        self.assertEqual(fill.normalize("⑩" * 200), "10" * 200)
        self.assertEqual(fill.normalize("9" * 400 + ".50"), "9" * 400 + ".5")

    def test_grade(self):
        g = fill.grade(["ア", "イ", "ウ"], fill.key_answers(KEY), {"ア": "仕入", "イ": "50000円", "ウ": "売掛金"})
        self.assertEqual({k: v["ok"] for k, v in g.items()}, {"ア": True, "イ": True, "ウ": False})
        g = fill.grade(["ア"], fill.key_answers(KEY), {})
        self.assertEqual(g["ア"], {"answer": "", "ok": False})


class FillRouteTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        make_bank(self.root)
        add_fill(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_loaded_and_routed(self):
        self.assertEqual(route.load_items(route.paths(self.root))["b-web-c0-f0"]["kind"], "fill")
        r = route.route(self.root, n=20, kinds=("fill",))
        self.assertEqual([it["id"] for it in r["items"]], ["b-web-c0-f0"])

    def test_stock_needs_fill_only_with_profile_section(self):
        st = route.stock(self.root)
        self.assertNotIn("fill", st["concepts"][0]["need"])
        prof = self.root / "exam" / "profile.md"
        prof.write_text(prof.read_text(encoding="utf-8") + "\n## 穴埋め問題\n\n- 答えの形：仕訳\n", encoding="utf-8")
        need = {r["concept"]: r["need"]["fill"] for r in route.stock(self.root)["concepts"]}
        self.assertEqual(need["web/c0"], route.FILL_TARGET - 1)
        self.assertEqual(need["web/c1"], route.FILL_TARGET)


if __name__ == "__main__":
    unittest.main()
