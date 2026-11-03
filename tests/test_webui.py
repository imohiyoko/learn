"""webui/server.py のテスト。claude の代わりに tests/fake_claude.py を使い、一時フォルダで動かす。"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webui"))

import server  # noqa: E402


class WebUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "examples", cls.tmp / "examples")
        for d in ("exam", "questions", "rubrics", "answers", "results", "log"):
            (cls.tmp / d).mkdir()
        server.configure(cls.tmp)
        os.environ["LEARN_CLAUDE"] = str(ROOT / "tests" / "fake_claude.py")
        cls.srv = server.make_server("127.0.0.1", 0, runner="cli")
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        server.configure(ROOT)
        shutil.rmtree(cls.tmp)

    def call(self, path, method="GET", body=None, headers=None):
        h = {"X-Learn": "1"}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_job(self, jid):
        for _ in range(100):
            _, r = self.call(f"/api/jobs/{jid}?wait=1")
            if r["job"]["status"] != "running":
                return r
            time.sleep(0.05)
        self.fail("終わらない")

    def test_01_flow(self):
        # 試験の設定前は出題できない
        _, st = self.call("/api/state")
        self.assertEqual(st["profile"], "missing")
        self.assertIn("sc", [e["key"] for e in st["examples"]])
        code, _ = self.call("/api/run", "POST", {"action": "question"})
        self.assertEqual(code, 409)

        # サンプルからコピー
        code, _ = self.call("/api/setup-example", "POST", {"key": "sc"})
        self.assertEqual(code, 200)
        _, st = self.call("/api/state")
        self.assertEqual(st["profile"], "ok")
        self.assertIn("web", [t["tag"] for t in st["tags"]])
        self.assertEqual(st["recommend"], {"written": [], "drill": []}, "おすすめは問題バンクから。バンクがなければ出さない")
        self.assertEqual(st["drill_count"], 10)

        # 出題：スキルをスラッシュコマンドで呼び、問題の id を拾う。採点基準の中身は流さない
        code, r = self.call("/api/run", "POST", {"action": "question", "tag": "web"})
        self.assertEqual(code, 200, r)
        r = self.wait_job(r["job"]["id"])
        self.assertEqual(r["job"]["question_ids"], ["20261103-1200-web"])
        _, r = self.call(f"/api/jobs/{r['job']['id']}")
        self.assertEqual(r["events"][0], {"type": "user", "text": "/question web", "seq": 0})
        self.assertNotIn("秘密", json.dumps(r["events"], ensure_ascii=False))
        self.assertTrue(r["job"]["can_reply"])

        # 続けて返信できる（--resume）
        jid = r["job"]["id"]
        code, _ = self.call(f"/api/jobs/{jid}/reply", "POST", {"text": "1ア 2ウ"})
        self.assertEqual(code, 200)
        r = self.wait_job(jid)
        _, r = self.call(f"/api/jobs/{jid}")
        self.assertIn("fake-session-1 の続き", r["events"][-3]["text"])

        # 問題は読めるが、採点基準は読めない
        code, f = self.call("/api/file?kind=questions&id=20261103-1200-web")
        self.assertEqual(code, 200)
        self.assertIn("設問1", f["text"])
        code, _ = self.call("/api/file?kind=rubrics&id=20261103-1200-web")
        self.assertEqual(code, 404)

        # 解答を保存して採点
        code, _ = self.call("/api/answers/20261103-1200-web", "PUT", {"text": "## 設問1\n\nこたえ"})
        self.assertEqual(code, 200)
        self.assertIn("こたえ", (self.tmp / "answers" / "20261103-1200-web.md").read_text(encoding="utf-8"))
        code, r = self.call("/api/run", "POST", {"action": "grade", "id": "20261103-1200-web"})
        self.assertEqual(code, 200)
        r = self.wait_job(r["job"]["id"])
        self.assertEqual(r["job"]["target"], "20261103-1200-web")
        code, f = self.call("/api/file?kind=results&id=20261103-1200-web")
        self.assertIn("採点結果", f["text"])

        _, items = self.call("/api/items")
        self.assertEqual(items["items"][0]["id"], "20261103-1200-web")

    def test_02_prompts(self):
        b = server.build_prompt
        self.assertEqual(b({"action": "question", "past": "past-r06a-q1"})[0], "/question past-r06a-q1")
        self.assertEqual(b({"action": "import", "year": "r06a", "q": "q1"})[0], "/import-past r06a q1")
        self.assertEqual(b({"action": "review"})[0], "/review")
        for bad in ({"action": "question", "tag": "web; rm -rf"}, {"action": "grade", "id": "../x"},
                    {"action": "grade", "id": "drill-1"},
                    {"action": "drill"},  # 択一ドリルは画面がバンクのセットにする（Claude にその場で作らせない）
                    {"action": "anything"}):
            with self.assertRaises(ValueError):
                b(bad)

    def test_03_guards(self):
        # 独自ヘッダーのない書き込み（ほかのサイトのフォームなど）は断る
        req = urllib.request.Request(self.base + "/api/profile", data=b'{"text":"x"}', method="PUT")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)
        # localhost 以外の Host（DNS rebinding）は断る
        code, _ = self.call("/api/state", headers={"Host": "evil.example:80"})
        self.assertEqual(code, 403)
        # フォルダの外は読めない
        code, _ = self.call("/api/file?kind=questions&id=../exam/profile")
        self.assertEqual(code, 404)
        code, _ = self.call("/api/answers/..%2Fexam%2Fprofile", "PUT", {"text": "x"})
        self.assertEqual(code, 404)

    def test_06_inbox(self):
        """--runner loop：依頼を inbox/ に置き、Claude Code（/inbox）の返事を読む。"""
        srv = server.make_server("127.0.0.1", 0, runner="loop")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base, self.base = self.base, f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            (self.tmp / "exam" / "profile.md").write_text(
                (self.tmp / "examples" / "sc" / "profile.md").read_text(encoding="utf-8"), encoding="utf-8")
            _, st = self.call("/api/state")
            self.assertEqual(st["runner"], "loop")
            code, r = self.call("/api/run", "POST", {"action": "question", "tag": "web"})
            self.assertEqual(code, 200, r)
            jid = r["job"]["id"]
            d = self.tmp / "inbox" / jid
            self.assertIn("/question web", (d / "000-user.md").read_text(encoding="utf-8"))
            _, r = self.call(f"/api/jobs/{jid}")
            self.assertEqual(r["job"]["status"], "running")
            self.assertEqual(r["events"][0]["text"], "/question web")
            # 返事が来るまでは返信できない
            code, _ = self.call(f"/api/jobs/{jid}/reply", "POST", {"text": "1ア"})
            self.assertEqual(code, 409)

            # /inbox スキルが返事を書いた
            (d / "000-claude.md").write_text("---\nquestions: [20261103-1200-web]\n---\n\n1問出しました。", encoding="utf-8")
            _, r = self.call(f"/api/jobs/{jid}?after=1")
            self.assertEqual(r["job"]["status"], "idle")
            self.assertEqual(r["job"]["question_ids"], ["20261103-1200-web"])
            self.assertEqual([e["type"] for e in r["events"]], ["text", "end"])
            self.assertEqual(r["events"][0]["text"], "1問出しました。")

            code, r = self.call(f"/api/jobs/{jid}/reply", "POST", {"text": "1ア 2ウ"})
            self.assertEqual(code, 200, r)
            self.assertIn("1ア 2ウ", (d / "001-user.md").read_text(encoding="utf-8"))
            _, jobs = self.call("/api/jobs")
            self.assertEqual(jobs["jobs"][0]["id"], jid)
            self.assertFalse(list(d.glob("*.tmp")))

            # 返信で /grade を頼んだら、その問題は採点中（解答の保存も、同じ問題の採点依頼も断る）
            qid = "20261103-1300-web"
            (self.tmp / "questions" / f"{qid}.md").write_text("---\nverified: true\n---\n# 問題", encoding="utf-8")
            (d / "001-claude.md").write_text("答え合わせしました。", encoding="utf-8")
            code, r = self.call(f"/api/jobs/{jid}/reply", "POST", {"text": f"/grade {qid}"})
            self.assertEqual(code, 200, r)
            self.assertEqual(r["job"]["target"], qid)
            code, _ = self.call(f"/api/answers/{qid}", "PUT", {"text": "書き直し"})
            self.assertEqual(code, 409)
            code, _ = self.call("/api/run", "POST", {"action": "grade", "id": qid})
            self.assertEqual(code, 409)

            # 4桁の番号も読める
            (d / "1000-user.md").write_text("続き", encoding="utf-8")
            _, r = self.call(f"/api/jobs/{jid}")
            self.assertEqual(r["events"][-1]["text"], "続き")
            code, _ = self.call("/api/jobs/..%2F..%2Fexam")
            self.assertEqual(code, 404)
        finally:
            self.base = base
            srv.shutdown()
            srv.server_close()
            server.make_server("127.0.0.1", 0, runner="cli").server_close()

    def test_05_describe_tool(self):
        d = server.describe_tool
        self.assertEqual(d("Bash", {"command": "cat > rubrics/x.md <<'EOF'\n正解はア\nEOF"}), "$ （採点基準の操作）")
        self.assertEqual(d("Bash", {"command": "python3 scripts/stats.py --next"}), "$ python3 scripts/stats.py --next")
        self.assertNotIn("秘密", d("Bash", {"command": "echo a\n秘密"}))
        self.assertEqual(d("Write", {"file_path": str(server.ROOT / "rubrics" / "a.md"), "content": "秘密"}), "Write rubrics/a.md")

    def test_06_port_fallback(self):
        # 使っているポートを指定すると、次の空いている番号で起動する
        busy = self.srv.server_address[1]
        if busy >= 65535:
            self.skipTest("末尾のポート")
        with self.assertRaises(SystemExit):
            server.make_server("127.0.0.1", busy, runner="cli").server_close()
        try:
            srv = server.make_server("127.0.0.1", busy, runner="cli", tries=20)
        except SystemExit:
            self.skipTest("近くのポートが埋まっている")
        try:
            self.assertGreater(srv.server_address[1], busy)
        finally:
            srv.server_close()

    def test_04_token(self):
        with self.assertRaises(SystemExit):
            server.make_server("0.0.0.0", 0)
        srv = server.make_server("127.0.0.1", 0, token="secret")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/state")
            self.assertEqual(cm.exception.code, 401)
            req = urllib.request.Request(base + "/api/state", headers={"Cookie": f"learn_token_{server.hashlib.sha256(b'secret').hexdigest()[:12]}=secret"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()
        # 後から作ったサーバーの設定が、先に動いているサーバーに混ざらない
        code, _ = self.call("/api/state")
        self.assertEqual(code, 200)

    def test_07_review_fixes(self):
        # 分野タグのない profile は未設定扱い
        prof = self.tmp / "exam" / "profile.md"
        saved = prof.read_text(encoding="utf-8") if prof.exists() else None
        prof.write_text("", encoding="utf-8")
        _, st = self.call("/api/state")
        self.assertEqual(st["profile"], "todo")
        code, _ = self.call("/api/run", "POST", {"action": "review"})
        self.assertEqual(code, 409)
        prof.write_text((self.tmp / "examples" / "sc" / "profile.md").read_text(encoding="utf-8"), encoding="utf-8")

        # 同じ記述問題を採点し直したら、履歴には最新の点数だけを出す
        qid = "20261103-0900-web"
        (self.tmp / "questions" / f"{qid}.md").write_text("---\ntags: [web]\n---\n本文", encoding="utf-8")
        rows = [{"id": qid, "source": "generated", "tags": ["web"], "score": s, "max": 100, "minutes": 30,
                 "graded_at": f"2026-11-03T0{h}:00:00+09:00", "weak": [], "miss_type": {}} for s, h in ((40, 1), (70, 2))]
        (self.tmp / "log" / "history.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        _, items = self.call("/api/items")
        self.assertEqual([x["score"] for x in items["items"] if x["id"] == qid], [[70, 100]])

        # 採点中は解答を保存できない
        os.environ["FAKE_CLAUDE_SLEEP"] = "1"
        try:
            code, r = self.call("/api/run", "POST", {"action": "grade", "id": qid})
            self.assertEqual(code, 200, r)
            code, _ = self.call(f"/api/answers/{qid}", "PUT", {"text": "x"})
            self.assertEqual(code, 409)
            self.wait_job(r["job"]["id"])
        finally:
            del os.environ["FAKE_CLAUDE_SLEEP"]
        code, _ = self.call(f"/api/answers/{qid}", "PUT", {"text": "x"})
        self.assertEqual(code, 200)

        # 想定外の失敗でも「実行中」のまま残らない
        for env in ({"LEARN_CLAUDE_ARGS": "--model 'unclosed"}, {"FAKE_CLAUDE_GARBAGE": "1"}):
            os.environ.update(env)
            try:
                code, r = self.call("/api/run", "POST", {"action": "review"})
                self.assertEqual(code, 200, r)
                r = self.wait_job(r["job"]["id"])
            finally:
                for k in env:
                    del os.environ[k]
            self.assertNotEqual(r["job"]["status"], "running")
        if saved is not None:
            prof.write_text(saved, encoding="utf-8")


    def upload(self, query, data, headers=None):
        h = {"X-Learn": "1", "Content-Type": "application/octet-stream"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + "/api/refs?" + query, data=data, method="POST", headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_08_refs_upload(self):
        from urllib.parse import quote
        name = quote("r06a 午後 q1.pdf")
        code, r = self.upload(f"dir=past&name={name}", b"%PDF-1.4 test")
        self.assertEqual(code, 200, r)
        self.assertEqual((self.tmp / "refs" / "past" / "r06a 午後 q1.pdf").read_bytes(), b"%PDF-1.4 test")
        # 同じ名前は、上書きを指定しない限り断る
        code, _ = self.upload(f"dir=past&name={name}", b"other")
        self.assertEqual(code, 409)
        code, _ = self.upload(f"dir=past&name={name}&overwrite=1", b"%PDF-1.5")
        self.assertEqual(code, 200)
        _, r = self.call("/api/refs")
        self.assertEqual([x["path"] for x in r["refs"]], ["past/r06a 午後 q1.pdf"])
        # フォルダの外・使えない種類・空・独自ヘッダーなしは断る
        for q in ("dir=past&name=..%2Fx.pdf", "dir=..&name=x.pdf", "dir=past&name=x.exe", "dir=past&name=.x.pdf"):
            code, _ = self.upload(q, b"x")
            self.assertEqual(code, 400, q)
        code, _ = self.upload("dir=past&name=empty.pdf", b"")
        self.assertEqual(code, 400)
        code, _ = self.upload("dir=past&name=y.pdf", b"x", {"X-Learn": "0"})
        self.assertEqual(code, 403)
        self.assertFalse((self.tmp / "x.pdf").exists())
        self.assertFalse((self.tmp / "refs" / "past" / "y.pdf").exists())
        # 一覧から外すのは refs/ 直下の README.md だけ
        (self.tmp / "refs" / "README.md").write_text("おすすめの置き方", encoding="utf-8")
        code, _ = self.upload("dir=past&name=README.md", b"memo")
        self.assertEqual(code, 200)
        _, r = self.call("/api/refs")
        self.assertEqual({x["path"] for x in r["refs"]}, {"past/r06a 午後 q1.pdf", "past/README.md"})

    def test_09_setup_exam_name(self):
        b = server.build_prompt
        self.assertEqual(b({"action": "setup", "exam": "応用情報技術者試験"})[0], "/setup-exam 応用情報技術者試験")
        self.assertEqual(b({"action": "setup"})[0], "/setup-exam")
        for bad in ("/review", "a\nb", "x" * 61):
            with self.assertRaises(ValueError):
                b({"action": "setup", "exam": bad})

    def test_10_ask_and_glossary(self):
        log = self.tmp / "log"
        for name in ("asks.jsonl", "glossary.jsonl", "glossary-state.json"):
            (log / name).unlink(missing_ok=True)
        qid = "20261103-1000-network"
        (self.tmp / "questions" / f"{qid}.md").write_text("---\ntags: [network, auth]\n---\n本文", encoding="utf-8")
        # 出題した会話（claude の代わりに fake_claude が答える）
        code, r = self.call("/api/run", "POST", {"action": "question", "tag": "network"})
        self.assertEqual(code, 200, r)
        jid = self.wait_job(r["job"]["id"])["job"]["id"]

        code, r = self.call("/api/ask", "POST", {"job": jid, "id": qid, "part": "設問1(2)", "kind": "word", "text": "DMARC の意味"})
        self.assertEqual(code, 200, r)
        r = self.wait_job(jid)
        self.assertIn("【解答中の質問】20261103-1000-network 設問1(2)\n種類：言葉の意味\nDMARC の意味",
                      [e.get("text") for e in r["events"] if e["type"] == "user"])
        rows = [json.loads(x) for x in (log / "asks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["tags"], ["network", "auth"])
        self.assertEqual(rows[0]["kind"], "word")
        # 種類・設問・問題がおかしい質問は記録しない
        for bad in ({"kind": "x"}, {"part": "設問1; rm"}, {"id": "nope"}, {"text": ""}):
            body = {"job": jid, "id": qid, "part": "", "kind": "ask", "text": "q", **bad}
            code, _ = self.call("/api/ask", "POST", body)
            self.assertIn(code, (400, 404), bad)
        self.assertEqual(len((log / "asks.jsonl").read_text(encoding="utf-8").splitlines()), 1)

        # まだ意味のない質問は pending、単語帳に入ったら回数に数える
        _, g = self.call("/api/glossary")
        self.assertEqual([p["text"] for p in g["pending"]], ["DMARC の意味"])
        (log / "glossary.jsonl").write_text(json.dumps({"term": "ＤＭＡＲＣ", "meaning": "", "tags": ["network"], "source": "ask"}, ensure_ascii=False)
                                            + "\n" + json.dumps({"term": "DMARC", "meaning": "送信ドメイン認証の方針", "tags": ["auth"], "source": "grade"}, ensure_ascii=False) + "\n",
                                            encoding="utf-8")
        _, g = self.call("/api/glossary")
        self.assertEqual(g["pending"], [])
        t = g["terms"][0]
        self.assertEqual((t["meaning"], t["tags"], t["asked"], t["known"]), ("送信ドメイン認証の方針", ["network", "auth"], 1, False))
        code, _ = self.call("/api/glossary", "POST", {"term": "SPF", "meaning": "", "tags": ["network", "bad tag"]})
        self.assertEqual(code, 200)
        code, _ = self.call("/api/glossary", "POST", {"term": "a\nb"})
        self.assertEqual(code, 400)
        code, _ = self.call("/api/glossary/state", "POST", {"term": "dmarc", "review": "ok"})
        self.assertEqual(code, 200)
        _, g = self.call("/api/glossary")
        self.assertEqual([(x["term"], x["known"], x["tags"]) for x in g["terms"]], [("SPF", False, ["network"]), ("DMARC", True, ["network", "auth"])])

        # 分析：質問は見込みに入れず、分野ごとの数として出す
        _, a = self.call("/api/analysis")
        self.assertEqual(a["analysis"]["asks"]["total"], 1)
        self.assertEqual(a["analysis"]["tags"]["network"]["asks"], {"word": 1})
        self.assertEqual(server.build_prompt({"action": "glossary", "terms": "DMARC、SPF"})[0], "/glossary DMARC、SPF")
        with self.assertRaises(ValueError):
            server.build_prompt({"action": "glossary", "terms": "/review"})


    def test_11_bank_sets(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import test_route
        for d in ("bank", "sets", "reports"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        (self.tmp / "log" / "bank.jsonl").unlink(missing_ok=True)
        # 論点と問題バンク（profile はテスト用のものを保つ）
        tmp2 = Path(tempfile.mkdtemp())
        try:
            test_route.make_bank(tmp2, tags=("web",), per=2)
            shutil.copytree(tmp2 / "bank", self.tmp / "bank")
            shutil.copy(tmp2 / "exam" / "concepts.md", self.tmp / "exam" / "concepts.md")
        finally:
            shutil.rmtree(tmp2)
        for f in (self.tmp / "bank" / "items").glob("*-q*.md"):
            f.write_text(f.read_text(encoding="utf-8") + "\n\n- ア 正しい\n- イ 違う\n- ウ 違う\n- エ 違う\n", encoding="utf-8")

        _, b = self.call("/api/bank")
        self.assertEqual((b["ready"], b["concepts"], b["items"]), (True, 2, 8))
        code, r = self.call("/api/sets", "POST", {"n": 2})
        self.assertEqual(code, 200, r)
        s = r["set"]
        self.assertEqual(len(s["items"]), 2)
        ch = next(it for it in s["items"] if it["kind"] == "choice")
        self.assertEqual([o["label"] for o in ch["options"]], ["ア", "イ", "ウ", "エ"])
        self.assertNotIn("- ア", ch["body"])
        self.assertNotIn("key", ch, "答える前に正解を出さない")
        # 選択肢にない答え・答え直しは断る
        code, _ = self.call(f"/api/sets/{s['id']}/answer", "POST", {"item": ch["id"], "value": "オ"})
        self.assertEqual(code, 400)
        code, r = self.call(f"/api/sets/{s['id']}/answer", "POST", {"item": ch["id"], "value": "イ"})
        self.assertEqual(code, 200, r)
        it = next(x for x in r["set"]["items"] if x["id"] == ch["id"])
        self.assertEqual((it["result"]["correct"], it["key"]["answer"]), (False, "ア"))
        code, _ = self.call(f"/api/sets/{s['id']}/answer", "POST", {"item": ch["id"], "value": "ア"})
        self.assertEqual(code, 409)
        rows = [json.loads(x) for x in (self.tmp / "log" / "bank.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(x["item"], x["score"], x["concepts"]) for x in rows], [(ch["id"], 0, ch["concepts"])])
        # 記述の正解（採点基準）は採点が終わるまで出さない
        short = [x for x in s["items"] if x["kind"] == "short"]
        if short:
            code, r = self.call(f"/api/sets/{s['id']}/answer", "POST", {"item": short[0]["id"], "value": "解答"})
            self.assertEqual(code, 200)
            self.assertNotIn("key", next(x for x in r["set"]["items"] if x["id"] == short[0]["id"]))
        code, r = self.call(f"/api/sets/{s['id']}/submit", "POST", {})
        self.assertEqual(code, 200, r)
        self.wait_job(r["job"]["id"])
        _, r = self.call(f"/api/sets/{s['id']}")
        self.assertEqual(r["set"]["status"], "graded")
        self.assertIn("解説です", r["set"]["report"])
        self.assertTrue(all("key" in x for x in r["set"]["items"]))
        self.assertEqual([c["concept"] for c in r["set"]["compare"]], ch["concepts"])
        code, _ = self.call(f"/api/sets/{s['id']}/answer", "POST", {"item": ch["id"], "value": "ア"})
        self.assertEqual(code, 409)
        _, r = self.call("/api/sets")
        self.assertEqual(r["sets"][0]["status"], "graded")
        # 分析にもバンクの択一が入る
        _, a = self.call("/api/analysis")
        self.assertEqual(a["analysis"]["attempts"]["drill"], 1)
        # 択一ドリル：バンクの択一だけのセット。在庫より多く頼んでも、ある分だけ出す（その場では作らない）
        code, r = self.call("/api/sets", "POST", {"n": 30, "kind": "choice", "tag": "web"})
        self.assertEqual(code, 200, r)
        dr = r["set"]
        self.assertEqual((dr["kind"], dr["want"]), ("choice", 30))
        self.assertTrue(dr["items"])
        self.assertTrue(all(x["kind"] == "choice" for x in dr["items"]))
        self.assertLess(len(dr["items"]), 30)
        code, _ = self.call("/api/sets", "POST", {"kind": "long"})
        self.assertEqual(code, 400)
        # おすすめも route.py の選び方から（長文はバンクにないので出さない）
        _, st = self.call("/api/state")
        self.assertEqual([x["tag"] for x in st["recommend"]["drill"]], ["web"])
        self.assertEqual(st["recommend"]["written"], [])
        # セットの外は読めない
        code, _ = self.call("/api/sets/..%2Fexam")
        self.assertEqual(code, 404)
        self.assertEqual(server.build_prompt({"action": "grade-set", "id": s["id"]})[0], f"/grade-set {s['id']}")
        with self.assertRaises(ValueError):
            server.build_prompt({"action": "grade-set", "id": "../x"})


if __name__ == "__main__":
    unittest.main()
