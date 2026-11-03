"""scripts/route.py のテスト。一時フォルダに論点・問題バンク・記録を作って選び方を確かめる。"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from learn import route  # noqa: E402

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone(timedelta(hours=9)))


def make_bank(root, tags=("web", "network"), per=3):
    (root / "exam").mkdir(parents=True)
    (root / "exam" / "profile.md").write_text(
        "## 分野タグ\n\n| タグ | 分野 | 重み |\n|---|---|---|\n" + "".join(f"| {t} | {t}分野 | 1.0 |\n" for t in tags),
        encoding="utf-8")
    rows = ["| タグ | 論点ID | 論点 | 重み |", "|---|---|---|---|"]
    for d in ("items", "keys"):
        (root / "bank" / d).mkdir(parents=True, exist_ok=True)
    for t in tags:
        for k in range(per):
            c = f"{t}/c{k}"
            rows.append(f"| {t} | {c} | {t}の論点{k} | 1.0 |")
            for j, diff in enumerate((1, 2, 3)):
                iid = f"b-{t}-c{k}-q{j}"
                (root / "bank" / "items" / f"{iid}.md").write_text(
                    f"---\nid: {iid}\nkind: choice\ntags: [{t}]\nconcepts: [{c}]\ndifficulty: {diff}\nverified: true\n---\n問題",
                    encoding="utf-8")
                (root / "bank" / "keys" / f"{iid}.md").write_text(f"---\nid: {iid}\nanswer: ア\n---\n解説", encoding="utf-8")
            iid = f"b-{t}-c{k}-s0"
            (root / "bank" / "items" / f"{iid}.md").write_text(
                f"---\nid: {iid}\nkind: short\ntags: [{t}]\nconcepts: [{c}]\ndifficulty: 2\nverified: true\n---\n問題", encoding="utf-8")
            (root / "bank" / "keys" / f"{iid}.md").write_text("---\nmax: 10\n---\n基準", encoding="utf-8")
    (root / "exam" / "concepts.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (root / "log").mkdir()


def log(root, rows):
    with open(root / "log" / "bank.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def row(item, concept, score, when, set_id="s1", kind="choice", diff=2):
    return {"id": f"{set_id}-{item}", "set": set_id, "item": item, "kind": kind, "tags": [concept.split("/")[0]],
            "concepts": [concept], "difficulty": diff, "score": score, "max": 1, "graded_at": when.isoformat(),
            "weak": [], "miss_type": {}}


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        make_bank(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_diagnose_first(self):
        r = route.route(self.root, n=6, now=NOW)
        self.assertEqual(r["phase"], "洗い出し")
        self.assertEqual(len(r["items"]), 6)
        concepts = [it["concepts"][0] for it in r["items"]]
        self.assertEqual(len(set(concepts)), 6, "同じ論点は1セットに1問")
        self.assertLessEqual(max(sum(c.startswith(t) for c in concepts) for t in ("web", "network")), 3, "1分野はセットの半分まで")
        self.assertTrue(all(it["difficulty"] == 2 for it in r["items"] if it["slot"] == "洗い出し"))
        # 同じ記録からは同じセット
        self.assertEqual(r, route.route(self.root, n=6, now=NOW))

    def test_remedy_and_retry(self):
        t0 = NOW - timedelta(days=5)
        rows = []
        for t in ("web", "network"):
            for k in range(3):
                good = not (t == "web" and k == 0)
                for j in range(6):  # 十分に測れた状態にする
                    rows.append(row(f"x-{t}{k}{j}", f"{t}/c{k}", 1 if good else 0, t0 + timedelta(minutes=j)))
        rows.append(row("b-web-c0-q1", "web/c0", 0, NOW - timedelta(days=2)))  # 1回間違えた → 1日後にやり直し
        log(self.root, rows)
        m = route.model(self.root)
        self.assertEqual(m["web/c0"]["status"], "苦手")
        r = route.route(self.root, n=4, now=NOW)
        self.assertEqual(r["phase"], "穴埋め")
        slots = {it["id"]: it["slot"] for it in r["items"]}
        self.assertEqual(slots.get("b-web-c0-q1"), "やり直し")
        # 苦手の穴埋めは、やり直しと同じ論点なので重ならない（1論点1問）
        self.assertEqual(sum(1 for it in r["items"] if it["concepts"][0] == "web/c0"), 1)
        # 苦手な論点には易しめの問題を選ぶ
        r2 = route.route(self.root, n=4, now=NOW, exclude=("b-web-c0-q1",))
        web0 = [it for it in r2["items"] if it["concepts"][0] == "web/c0"]
        self.assertEqual(web0[0]["slot"], "穴埋め")
        self.assertEqual(web0[0]["difficulty"], 1)

    def test_retry_waits(self):
        log(self.root, [row("b-web-c0-q1", "web/c0", 0, NOW - timedelta(hours=3))])
        r = route.route(self.root, n=10, now=NOW)
        self.assertNotIn("b-web-c0-q1", [it["id"] for it in r["items"]], "1日たつまではやり直さない")

    def test_stock_and_compare(self):
        s = route.stock(self.root)
        self.assertEqual(s["total_need"], 2, "長文が分野ごとに1問ずつ足りない")
        (self.root / "bank" / "items" / "b-web-c0-q0.md").unlink()
        s = route.stock(self.root)
        self.assertEqual([r["need"] for r in s["concepts"] if r["concept"] == "web/c0"], [{"choice": 1, "short": 0}])
        log(self.root, [row("b-web-c1-q1", "web/c1", 1, NOW, set_id="s9")])
        c = route.compare("s9", self.root)
        self.assertEqual([(x["concept"], x["before"]) for x in c], [("web/c1", 0.5)])
        self.assertGreater(c[0]["after"], 0.5)


if __name__ == "__main__":
    unittest.main()
