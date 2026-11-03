"""記録の形（schema/）が、テンプレート・web UI の書く行と合っているかのテスト。

schema/ は learn の記録の形の正。imohiyoko/learning の取り込みもこれに合わせる（docs/formats.md）。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "webui"))

import schema_lite  # noqa: E402
import server  # noqa: E402
from test_route import make_bank  # noqa: E402

# テンプレート → スキーマ
TEMPLATES = {
    "history-line.json": "history-line",
    "bank-line.json": "bank-line",
    "drill-line.json": "drill-line",
    "ask-line.json": "ask-line",
    "glossary-line.json": "glossary-line",
}


def jsonl(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class SchemaTest(unittest.TestCase):
    def test_every_template_has_schema(self):
        lines = {p.name for p in (ROOT / "templates").glob("*-line.json")}
        self.assertEqual(lines, set(TEMPLATES), "templates/*-line.json を足したら schema/ と TEMPLATES にも足す")
        schemas = {p.name.replace(".schema.json", "") for p in (ROOT / "schema").glob("*.schema.json")}
        self.assertEqual(schemas, set(TEMPLATES.values()))

    def test_templates_match_schema(self):
        for tpl, name in TEMPLATES.items():
            with self.subTest(tpl):
                row = json.loads((ROOT / "templates" / tpl).read_text(encoding="utf-8"))
                self.assertEqual(schema_lite.errors(row, schema_lite.load(name)), [])

    def test_validator_rejects(self):
        s = schema_lite.load("history-line")
        ok = json.loads((ROOT / "templates" / "history-line.json").read_text(encoding="utf-8"))
        for broken in ({**ok, "score": "62"}, {**ok, "max": 0}, {**ok, "tags": []},
                       {**ok, "graded_at": "2026-10-25 22:20"}, {**ok, "extra": 1},
                       {k: v for k, v in ok.items() if k != "id"}):
            with self.subTest(broken):
                self.assertNotEqual(schema_lite.errors(broken, s), [])

    def test_history_fields_known_to_learning(self):
        # learning の `import history` は知らない項目のある行を読まない（DisallowUnknownFields）。
        # ここに項目を足すときは、learning の internal/practice/history.go も直す
        props = set(schema_lite.load("history-line")["properties"])
        self.assertEqual(props, {"id", "source", "tags", "score", "max", "minutes", "graded_at", "weak", "miss_type",
                                 "kind", "concepts", "difficulty"})


class ServerLinesTest(unittest.TestCase):
    """web UI のサーバーが書く行がスキーマに合うか。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        make_bank(self.tmp)
        for f in (self.tmp / "bank" / "items").glob("*-q*.md"):  # 択一に選択肢を足す
            f.write_text(f.read_text(encoding="utf-8") + "\n\n- ア 一つ目\n- イ 二つ目\n", encoding="utf-8")
        for d in ("questions", "log"):
            (self.tmp / d).mkdir(exist_ok=True)
        (self.tmp / "questions" / "q1.md").write_text(
            "---\nid: q1\nsource: generated        # generated | past\ntags: [web, auth]\n---\n# 問\n", encoding="utf-8")
        server.configure(self.tmp)

    def tearDown(self):
        server.configure(ROOT)
        shutil.rmtree(self.tmp)

    def check(self, log, name):
        rows = jsonl(self.tmp / "log" / log)
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(schema_lite.errors(r, schema_lite.load(name)), [], r)

    def test_ask(self):
        server.record_ask("q1", "設問1(2)", "word", "p=none の意味")
        server.record_ask("q1", None, "worry", "時間が足りない")
        st = server.create_set(n=2, kind="choice")
        server.ask_set_word(st["id"], st["items"][0]["id"], "SameSite")
        self.check("asks.jsonl", "ask-line")

    def test_glossary(self):
        server.add_term("DMARC", "送信ドメイン認証の結果の扱いを宣言する仕組み。", ["network"])
        self.check("glossary.jsonl", "glossary-line")

    def test_bank_choice(self):
        s = server.create_set(n=4, kind="choice")
        for it in s["items"]:
            server.answer_set_item(s["id"], it["id"], "ア")
        self.check("bank.jsonl", "bank-line")

    def test_bank_choice_confidence(self):
        s = server.create_set(n=3, kind="choice")
        a, b, c = [it["id"] for it in s["items"]]
        server.answer_set_item(s["id"], a, "ア", "guess")
        server.answer_set_item(s["id"], b, "ア", "sure")
        with self.assertRaises(ValueError):
            server.answer_set_item(s["id"], c, "ア", "maybe")
        server.answer_set_item(s["id"], c, "ア")  # 確信度なしの古い呼び出しも受ける
        self.check("bank.jsonl", "bank-line")
        rows = [json.loads(x) for x in (server.ROOT / "log" / "bank.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r.get("confidence") for r in rows], ["guess", "sure", None])


if __name__ == "__main__":
    unittest.main()
