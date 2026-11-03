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
        cls.srv = server.make_server("127.0.0.1", 0)
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
        self.assertTrue(st["recommend"]["written"])
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
        self.assertEqual(b({"action": "drill", "tag": "auth", "count": "5"})[0], "/drill auth 5")
        self.assertEqual(b({"action": "drill"})[0], "/drill")
        self.assertEqual(b({"action": "question", "past": "past-r06a-q1"})[0], "/question past-r06a-q1")
        self.assertEqual(b({"action": "import", "year": "r06a", "q": "q1"})[0], "/import-past r06a q1")
        self.assertEqual(b({"action": "review"})[0], "/review")
        for bad in ({"action": "question", "tag": "web; rm -rf"}, {"action": "grade", "id": "../x"},
                    {"action": "grade", "id": "drill-1"}, {"action": "drill", "count": 999},
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

    def test_05_describe_tool(self):
        d = server.describe_tool
        self.assertEqual(d("Bash", {"command": "cat > rubrics/x.md <<'EOF'\n正解はア\nEOF"}), "$ （採点基準の操作）")
        self.assertEqual(d("Bash", {"command": "python3 scripts/stats.py --next"}), "$ python3 scripts/stats.py --next")
        self.assertNotIn("秘密", d("Bash", {"command": "echo a\n秘密"}))
        self.assertEqual(d("Write", {"file_path": str(server.ROOT / "rubrics" / "a.md"), "content": "秘密"}), "Write rubrics/a.md")

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
            req = urllib.request.Request(base + "/api/state", headers={"Cookie": "learn_token=secret"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()
            server.Handler.token = None
            server.Handler.allowed_hosts = server.LOOPBACK


if __name__ == "__main__":
    unittest.main()
