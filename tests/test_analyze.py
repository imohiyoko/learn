import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import analyze  # noqa: E402

TAGS = {"web": ("Web", 1.5), "net": ("ネットワーク", 1.0), "crypto": ("暗号", 0.8), "law": ("法令", 0.5)}


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def written(i, tag, score, **kw):
    return dict({"id": f"w{i}", "tags": [tag], "score": score, "max": 100,
                 "graded_at": f"2026-10-01T{i:02d}:00"}, **kw)


def drill(i, tag, ok, **kw):
    return dict({"id": f"d{i}", "source": "drill", "tags": [tag], "score": int(ok), "max": 1,
                 "graded_at": f"2026-10-02T{i // 60:02d}:{i % 60:02d}"}, **kw)


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_analyze(self, hist, dr, pass_ratio=0.6):
        h, d = self.dir / "history.jsonl", self.dir / "drill.jsonl"
        write_jsonl(h, hist)
        write_jsonl(d, dr)
        return analyze.analyze(TAGS, analyze.load_rows(h, d), pass_ratio)

    def test_strong_weak_and_untouched(self):
        dr = [drill(i, "net", True) for i in range(12)] + [drill(100 + i, "crypto", False) for i in range(12)]
        a = self.run_analyze([], dr)
        self.assertEqual(a["tags"]["net"]["status"], "得意")
        self.assertTrue(a["tags"]["net"]["sure"])
        self.assertEqual(a["tags"]["crypto"]["status"], "苦手")
        self.assertTrue(a["tags"]["crypto"]["sure"])
        self.assertEqual(a["tags"]["law"]["status"], "未出題")
        self.assertEqual(a["attempts"], {"written": 0, "drill": 24})

    def test_few_records_are_measuring(self):
        a = self.run_analyze([written(1, "web", 90)], [drill(1, "web", True)])
        self.assertEqual(a["tags"]["web"]["status"], "測定中")
        self.assertIsNone(a["tags"]["web"]["basis"])

    def test_written_weakness_not_hidden_by_drill(self):
        """択一で満点でも、記述式で落としていれば苦手と判定する。"""
        hist = [written(i, "web", 30, miss_type={"writing": 2}) for i in range(4)]
        dr = [drill(i, "web", True) for i in range(15)]
        s = self.run_analyze(hist, dr)["tags"]["web"]
        self.assertEqual(s["status"], "苦手")
        self.assertEqual(s["basis"], "written")
        self.assertEqual(s["gap"], "knows_but_cannot_write")
        self.assertEqual(s["main_miss"], "writing")

    def test_sub_tag_counts_half(self):
        hist = [dict(written(i, "web", 80), tags=["web", "net"]) for i in range(4)]
        s = self.run_analyze(hist, [])["tags"]
        self.assertEqual(s["web"]["written"]["evidence"], 4.0)
        self.assertEqual(s["net"]["written"]["evidence"], 2.0)
        self.assertEqual(s["net"]["written"]["n"], 0)  # 主タグとしては出ていない

    def test_trend(self):
        hist = [written(i, "web", sc) for i, sc in enumerate([20, 30, 70, 80])]
        self.assertEqual(self.run_analyze(hist, [])["tags"]["web"]["written"]["trend"], "up")
        hist = [written(i, "web", sc) for i, sc in enumerate([80, 70, 30, 20])]
        self.assertEqual(self.run_analyze(hist, [])["tags"]["web"]["written"]["trend"], "down")

    def test_recent_results_weigh_more(self):
        old_bad = [drill(i, "net", False) for i in range(10)] + [drill(10 + i, "net", True) for i in range(10)]
        old_good = [drill(i, "net", True) for i in range(10)] + [drill(10 + i, "net", False) for i in range(10)]
        p1 = self.run_analyze([], old_bad)["tags"]["net"]["p"]
        p2 = self.run_analyze([], old_good)["tags"]["net"]["p"]
        self.assertGreater(p1, p2)

    def test_repeated_weak_terms(self):
        dr = [drill(i, "web", False, weak=["SameSite 属性"]) for i in range(2)] + \
             [drill(5, "net", False, weak=["SameSite属性"]), drill(6, "net", False, weak=["DNSSEC"])]
        rep = self.run_analyze([], dr)["repeated_weak"]
        self.assertEqual(len(rep), 1)
        self.assertEqual(rep[0]["count"], 3)
        self.assertEqual(rep[0]["tags"], ["web", "net"])

    def test_unknown_tag_is_reported(self):
        a = self.run_analyze([], [drill(i, "misc", True) for i in range(6)])
        self.assertEqual(a["tags"]["misc"]["name"], "(profile未登録)")

    def test_render_does_not_crash(self):
        hist = [written(i, "web", 40, weak=["根拠"], miss_type={"reading": 1}) for i in range(4)]
        dr = [drill(i, t, i % 3 == 0) for i, t in enumerate(["net", "crypto"] * 10)]
        text = analyze.render(self.run_analyze(hist, dr))
        self.assertIn("苦手", text)
        self.assertIn("reading", text)
        self.assertIn("まだ記録がありません", analyze.render(self.run_analyze([], [])))


    def test_bad_rows_are_skipped(self):
        h, d = self.dir / "history.jsonl", self.dir / "drill.jsonl"
        h.write_text('{"tags":["web"],"score":NaN,"max":100}\n'
                     '{"tags":["web"],"score":50,"max":Infinity}\n'
                     '{"tags":["web"],"score":50,"max":100,"weak":"根拠"}\n'
                     '{"tags":["web"],"score":50,"max":100,"graded_at":"2026-10-01T10:00"}\n', encoding="utf-8")
        write_jsonl(d, [])
        rows = analyze.load_rows(h, d)
        self.assertEqual(len(rows), 1)

    def test_rows_sorted_by_time_across_offsets(self):
        # 文字列では UTC の方が先に並ぶが、時刻では +09:00 の方が先
        hist = [written(1, "web", 10, graded_at="2026-10-01T10:00:00+09:00"),
                written(2, "web", 90, graded_at="2026-10-01T02:00:00Z")]
        h, d = self.dir / "history.jsonl", self.dir / "drill.jsonl"
        write_jsonl(h, hist)
        write_jsonl(d, [])
        self.assertEqual([r["id"] for r in analyze.load_rows(h, d)], ["w1", "w2"])

    def test_duplicates_in_a_row_count_once(self):
        hist = [dict(written(i, "web", 50), tags=["web", "web"], weak=["根拠", "根拠 "]) for i in range(2)]
        a = self.run_analyze(hist, [])
        self.assertEqual(a["tags"]["web"]["written"]["evidence"], 2.0)
        self.assertEqual(a["repeated_weak"][0]["count"], 2)

    def test_tag_weak_terms_merge_spacing(self):
        hist = [written(1, "web", 30, weak=["SameSite 属性"]), written(2, "web", 30, weak=["SameSite属性"])]
        self.assertEqual(self.run_analyze(hist, [])["tags"]["web"]["weak"], ["SameSite 属性"])

    def test_fractional_miss_below_one_is_ignored(self):
        s = self.run_analyze([written(1, "web", 30, miss_type={"reading": 0.5})], [])
        self.assertEqual(s["tags"]["web"]["miss"], {})
        self.assertEqual(s["miss_type"], {})

    def test_huge_int_is_skipped(self):
        h, d = self.dir / "history.jsonl", self.dir / "drill.jsonl"
        h.write_text('{"tags":["web"],"score":1,"max":' + "9" * 400 + '}\n', encoding="utf-8")
        write_jsonl(d, [])
        self.assertEqual(analyze.load_rows(h, d), [])

    def test_overall_is_capped(self):
        a = self.run_analyze([written(1, "web", 120)], [])
        self.assertEqual(a["overall"]["written_avg"], 1.0)


class PassRatioTest(unittest.TestCase):
    def parse(self, line):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "profile.md"
            p.write_text(f"# x\n\n- {line}\n" if not line.startswith("#") else line, encoding="utf-8")
            return analyze.parse_pass_ratio(p)

    def test_formats(self):
        self.assertAlmostEqual(self.parse("合格基準：科目A-1・A-2・Bそれぞれ100点満点中60点以上"), 0.6)
        self.assertAlmostEqual(self.parse("合格基準：150点満点中90点以上"), 0.6)
        self.assertAlmostEqual(self.parse("合格基準：正答率65%以上"), 0.65)
        self.assertAlmostEqual(self.parse("合格基準：7割"), 0.7)
        self.assertAlmostEqual(self.parse("合格基準：<!-- TODO 例：100点満点中80点以上 -->"), 0.6)
        self.assertAlmostEqual(analyze.parse_pass_ratio(Path("/nonexistent/profile.md")), 0.6)

    def test_ignores_comments_and_prose(self):
        text = ("# x\n\n<!--\n- 合格基準：80%\n-->\n"
                "前回の合格基準は90%だった。\n- 合格基準：70%\n")
        self.assertAlmostEqual(self.parse(text), 0.7)


if __name__ == "__main__":
    unittest.main()
