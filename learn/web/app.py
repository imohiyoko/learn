"""ブラウザから学習を回すためのローカルサーバー（HTTP の入口と起動）。標準ライブラリのみ。
入口は webui/server.py。

出題・採点などの中身は、これまでどおり Claude Code のスキル（.claude/skills/）が行う。
このサーバーは、ブラウザで選んだ内容を Claude Code に渡し、返事をブラウザに見せるだけ。
学習データはこれまでと同じファイル（questions/ log/ など）に残る。渡し方は2つ：

  --runner loop（既定）  依頼を inbox/ に置く。このフォルダで動かしている Claude Code が
                         `/loop 1m /inbox` で拾って処理し、返事を inbox/ に書く
  --runner cli           このサーバーが `claude -p "/question web"` を起動して処理する
                         （常駐の Claude Code を用意しないとき。API キーでも動く）

使い方:
  python3 webui/server.py                 http://127.0.0.1:4870 で起動（使用中なら 4871, 4872, ... を試す）
  python3 webui/server.py --port 9000
  python3 webui/server.py --runner cli    claude -p で処理する
  LEARN_TOKEN=xxxx python3 webui/server.py --host 0.0.0.0
                                          同じLANのスマホから使う（トークン必須）

環境変数:
  LEARN_TOKEN        アクセス用のトークン。127.0.0.1 以外で待ち受けるときは必須
  LEARN_CLAUDE       claude コマンドのパス（既定：claude）
  LEARN_CLAUDE_ARGS  claude に渡す追加の引数（例：--model sonnet）
"""
import argparse
import errno
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from learn.web import files
from learn.web.files import safe_child
from learn.web.glossary import add_term, ask_message, glossary, record_ask, update_term_state
from learn.web.home import analysis, list_items, profile_status, state
from learn.web.jobs import InboxJobs, Jobs, build_prompt, claude_bin
from learn.web.refs import REF_DIRS, REF_MAX, list_refs, save_ref
from learn.web.sets import answer_set_item, ask_set_word, bank_status, create_set, list_sets, load_set, save_set, set_view, submit_set


STATIC = Path(__file__).resolve().parents[2] / "webui" / "static"

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


class Handler(BaseHTTPRequestHandler):
    server_version = "learn-webui"
    token = None
    allowed_hosts = None
    jobs = None
    runner = None

    def log_message(self, fmt, *args):
        if os.environ.get("LEARN_VERBOSE"):
            super().log_message(fmt, *args)

    # -- 応答
    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if path.suffix.lower() == ".svg":
            self.send_header("Content-Security-Policy", "script-src 'none'")
        self.end_headers()
        self.wfile.write(data)

    def error(self, status, msg):
        self.send_json({"error": msg}, status)

    # -- 認証と、ほかのサイトからの呼び出しの防止
    def host_ok(self):
        if self.allowed_hosts is None:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in self.allowed_hosts

    def cookie_name(self):
        # Cookie はポートを区別しないので、同じ PC で別のトークンのサーバーを並べても混ざらないよう名前を分ける
        return "learn_token_" + hashlib.sha256(self.token.encode()).hexdigest()[:12]

    def authed(self):
        if not self.token:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        name = self.cookie_name()
        given = cookie[name].value if name in cookie else ""
        given = self.headers.get("X-Learn-Token") or given
        return hmac.compare_digest(given, self.token)

    def guard(self, write=False):
        if not self.host_ok():
            self.error(HTTPStatus.FORBIDDEN, "このホスト名では使えません")
            return False
        if not self.authed():
            self.error(HTTPStatus.UNAUTHORIZED, "トークンが必要です（URL の末尾に ?token=... を付けて開いてください）")
            return False
        # 書き込み系は、フォームなどから勝手に送れないよう独自ヘッダーを必須にする
        if write and self.headers.get("X-Learn") != "1":
            self.error(HTTPStatus.FORBIDDEN, "不正なリクエストです")
            return False
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("大きすぎます")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    # -- GET
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path

        if self.token and "token" in q and hmac.compare_digest(q["token"], self.token) and self.host_ok():
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", f"{self.cookie_name()}={self.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
            self.send_header("Location", path)
            self.end_headers()
            return

        if not path.startswith("/api/") and not path.startswith("/assets/"):
            if not self.host_ok():
                return self.error(HTTPStatus.FORBIDDEN, "このホスト名では使えません")
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            p = (STATIC / name).resolve()
            if p.parent != STATIC.resolve() or not p.is_file():
                return self.error(404, "not found")
            if name == "index.html" and not self.authed():
                return self.error(HTTPStatus.UNAUTHORIZED, "トークンが必要です（URL の末尾に ?token=... を付けて開いてください）")
            return self.send_file(p)

        if not self.guard():
            return
        try:
            if path == "/api/state":
                return self.send_json(state(self.jobs, self.runner))
            if path == "/api/analysis":
                return self.send_json({"analysis": analysis()})
            if path == "/api/items":
                return self.send_json({"items": list_items()})
            if path == "/api/file":
                base = files.VIEWABLE.get(q.get("kind", ""))
                p = safe_child(base, q.get("id", "") + ".md") if base else None
                if not p or not p.is_file():
                    return self.error(404, "ファイルがありません")
                return self.send_json({"id": q["id"], "kind": q["kind"], "text": p.read_text(encoding="utf-8")})
            if path == "/api/profile":
                return self.send_json({"text": files.PROFILE.read_text(encoding="utf-8") if files.PROFILE.exists() else None})
            if path == "/api/jobs":
                return self.send_json({"jobs": self.jobs.list()})
            if path == "/api/refs":
                return self.send_json({"refs": list_refs(), "dirs": list(REF_DIRS)})
            if path == "/api/glossary":
                return self.send_json(glossary())
            if path == "/api/bank":
                return self.send_json(bank_status())
            if path == "/api/sets":
                return self.send_json({"sets": list_sets()})
            m = re.match(r"^/api/sets/([^/]+)$", path)
            if m:
                s = load_set(m.group(1))
                return self.send_json({"set": set_view(s)}) if s else self.error(404, "そのセットはありません")
            m = re.match(r"^/api/jobs/([0-9a-z-]+)$", path)
            if m:
                r = self.jobs.events(m.group(1), int(q.get("after", 0)), min(float(q.get("wait", 0) or 0), 30))
                if not r:
                    return self.error(404, "見つかりません")
                return self.send_json({"job": r[0], "events": r[1]})
            if path.startswith("/assets/"):
                p = safe_child(files.ASSETS, path[len("/assets/"):])
                if not p or not p.is_file() or p.suffix.lower() not in CONTENT_TYPES:
                    return self.error(404, "not found")
                return self.send_file(p)
        except Exception as e:  # 想定外でも画面は落とさない
            return self.error(500, f"{type(e).__name__}: {e}")
        return self.error(404, "not found")

    # -- POST / PUT
    def do_POST(self):
        if not self.guard(write=True):
            return
        url = urlparse(self.path)
        path = url.path
        if path == "/api/refs":
            return self.upload_ref({k: v[0] for k, v in parse_qs(url.query).items()})
        try:
            body = self.body()
            if path == "/api/run":
                prompt, title, *target = build_prompt(body)
                if body.get("action") != "setup" and profile_status() != "ok":
                    return self.error(409, "先に試験の設定をしてください")
                with self.jobs.lock:
                    if target and self.jobs.grading(target[0]):
                        return self.error(409, "その問題はいま採点中です")
                    job = self.jobs.start(prompt, title, *target)
                return self.send_json({"job": job if isinstance(job, dict) else job.summary()})
            m = re.match(r"^/api/jobs/([0-9a-z-]+)/reply$", path)
            if m:
                text = str(body.get("text") or "").strip()
                if not text:
                    return self.error(400, "空です")
                return self.send_json({"job": self.jobs.reply(m.group(1), text[:20000])})
            if path == "/api/ask":
                # 解答中の質問：記録してから、出題した会話の続きとして送る
                qid, part = str(body.get("id") or ""), str(body.get("part") or "").strip()
                kind, text = str(body.get("kind") or ""), str(body.get("text") or "").strip()
                jid = str(body.get("job") or "")
                with self.jobs.lock:
                    if not self.jobs.can_reply(jid):
                        return self.error(409, "前の返事を待っています。届いてからもう一度どうぞ")
                    record_ask(qid, part, kind, text)
                    job = self.jobs.reply(jid, ask_message(qid, part, kind, text))
                return self.send_json({"job": job})
            if path == "/api/sets":
                if profile_status() != "ok":
                    return self.error(409, "先に試験の設定をしてください")
                return self.send_json({"set": set_view(create_set(int(body.get("n") or 10), str(body.get("tag") or ""),
                                                                  str(body.get("kind") or "mixed")))})
            m = re.match(r"^/api/sets/([^/]+)/answer$", path)
            if m:
                s = answer_set_item(m.group(1), str(body.get("item") or ""), body.get("value"))
                return self.send_json({"set": set_view(s)})
            m = re.match(r"^/api/sets/([^/]+)/ask$", path)
            if m:
                # セットを解いている途中の「わからない言葉」：記録して、/glossary で意味を作る
                term = str(body.get("term") or "").strip()
                with self.jobs.lock:
                    r = ask_set_word(m.group(1), str(body.get("item") or ""), term, bool(body.get("again")))
                    if r["deferred"] or r["meaning"]:
                        return self.send_json(r)
                    try:
                        job = self.jobs.start(f"/glossary {term}", f"単語帳 {term}")
                    except RuntimeError:
                        # Claude が別の作業中。言葉は記録したので、単語帳からあとで意味を作れる
                        return self.send_json({"deferred": False, "busy": True})
                return self.send_json({"deferred": False, "job": job if isinstance(job, dict) else job.summary()})
            m = re.match(r"^/api/sets/([^/]+)/submit$", path)
            if m:
                sid = m.group(1)
                with self.jobs.lock:
                    if self.jobs.grading(sid):
                        return self.error(409, "このセットはいま採点中です")
                    s = submit_set(sid)
                    try:
                        job = self.jobs.start(f"/grade-set {sid}", f"セットの採点 {sid}", sid)
                    except RuntimeError:
                        s["status"] = "open"  # Claude が別の作業中。提出は取り消して、あとでもう一度
                        save_set(s)
                        raise
                return self.send_json({"set": set_view(s), "job": job if isinstance(job, dict) else job.summary()})
            if path == "/api/glossary":
                add_term(body.get("term"), body.get("meaning"), body.get("tags"))
                return self.send_json({"ok": True})
            if path == "/api/glossary/state":
                return self.send_json({"state": update_term_state(body.get("term"), body)})
            m = re.match(r"^/api/jobs/([0-9a-z-]+)/stop$", path)
            if m:
                self.jobs.stop(m.group(1))
                return self.send_json({"ok": True})
            if path == "/api/setup-example":
                key = str(body.get("key") or "")
                src = safe_child(files.EXAMPLES, key)
                if not src or not (src / "profile.md").is_file():
                    return self.error(404, "その例はありません")
                if files.PROFILE.exists() and not body.get("overwrite"):
                    return self.error(409, "exam/profile.md がすでにあります")
                files.PROFILE.parent.mkdir(exist_ok=True)
                shutil.copyfile(src / "profile.md", files.PROFILE)
                return self.send_json({"ok": True})
        except LookupError as e:
            return self.error(404, str(e))
        except (ValueError, RuntimeError) as e:
            return self.error(400 if isinstance(e, ValueError) else 409, str(e))
        except Exception as e:
            return self.error(500, f"{type(e).__name__}: {e}")
        return self.error(404, "not found")

    def upload_ref(self, q):
        """POST /api/refs?dir=past&name=<ファイル名>[&overwrite=1]。本文はファイルの中身そのまま"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        try:
            if n > REF_MAX:
                self.close_connection = True  # 読まずに断るので、残りの本文で次の要求を壊さない
            saved = save_ref(q.get("dir", "past"), q.get("name", ""), self.rfile.read, n, q.get("overwrite") == "1")
            return self.send_json({"ok": True, "path": saved})
        except FileExistsError as e:
            self.close_connection = True
            return self.error(409, str(e))
        except ValueError as e:
            self.close_connection = True
            return self.error(400, str(e))
        except Exception as e:
            self.close_connection = True
            return self.error(500, f"{type(e).__name__}: {e}")

    def do_PUT(self):
        if not self.guard(write=True):
            return
        path = urlparse(self.path).path
        try:
            body = self.body()
            text = body.get("text")
            if not isinstance(text, str):
                return self.error(400, "text がありません")
            if path == "/api/profile":
                files.PROFILE.parent.mkdir(exist_ok=True)
                files.PROFILE.write_text(text, encoding="utf-8")
                return self.send_json({"ok": True})
            m = re.match(r"^/api/answers/([^/]+)$", path)
            if m:
                qid = m.group(1)
                p = safe_child(files.VIEWABLE["answers"], qid + ".md")
                if not p or not (files.VIEWABLE["questions"] / f"{qid}.md").is_file():
                    return self.error(404, "その問題はありません")
                with self.jobs.lock:  # 確認と保存の間に採点が始まらないように
                    if self.jobs.grading(qid):
                        return self.error(409, "採点中なので、いまは保存できません")
                    p.parent.mkdir(exist_ok=True)
                    p.write_text(text, encoding="utf-8")
                return self.send_json({"ok": True})
        except Exception as e:
            return self.error(500, f"{type(e).__name__}: {e}")
        return self.error(404, "not found")


LOOPBACK = {"127.0.0.1", "localhost", "::1"}

RUNNERS = {"loop": InboxJobs, "cli": Jobs}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows の SO_REUSEADDR は使用中のポートにも重ねて bind できてしまい、空きを確かめられないので使わない
    allow_reuse_address = os.name != "nt"


class Server6(Server):
    address_family = socket.AF_INET6


DEFAULT_PORT = 4870

PORT_TRIES = 20  # 使用中なら次の番号を順に試す数


def make_server(host="127.0.0.1", port=DEFAULT_PORT, token=None, runner="loop", tries=1):
    if not 0 <= port <= 65535:
        raise SystemExit("--port は 0〜65535 で指定してください（1024〜49151 が無難）")
    if runner not in RUNNERS:
        raise SystemExit(f"--runner は {' / '.join(RUNNERS)} のどれかです")
    if host not in LOOPBACK and not token:
        raise SystemExit("127.0.0.1 以外で待ち受けるときは LEARN_TOKEN を設定してください")
    # 設定はサーバーごとに持つ（同じプロセスで複数作っても混ざらないように）
    handler = type("BoundHandler", (Handler,), {
        "token": token or None,
        "runner": runner,
        "jobs": RUNNERS[runner](),
        # 127.0.0.1 で待ち受けるときは、Host が localhost のリクエストだけを受ける（DNS rebinding 対策）
        "allowed_hosts": LOOPBACK if (host in LOOPBACK or not token) else None,
    })
    cls = Server6 if ":" in host else Server
    for p in range(port, min(port + max(tries, 1), 65536)):
        try:
            return cls((host, p), handler)
        except OSError as e:
            # 使用中（EADDRINUSE / Windows の WSAEADDRINUSE・WSAEACCES）なら次の番号へ。
            # Windows 以外の EACCES は権限不足（1023 以下のポートなど）なので、そのまま止める
            in_use = e.errno in (errno.EADDRINUSE, 10048) or (os.name == "nt" and e.errno in (errno.EACCES, 10013))
            if not in_use or port == 0:
                raise
            last = e
    raise SystemExit(f"ポート {port}〜{p} はすべて使用中です。--port で別の番号を指定してください（{last}）")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("LEARN_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LEARN_PORT", DEFAULT_PORT)),
                    help=f"待ち受けるポート（既定：{DEFAULT_PORT}）。使用中なら次の番号を順に試す")
    ap.add_argument("--runner", choices=sorted(RUNNERS), default=os.environ.get("LEARN_RUNNER", "loop"),
                    help="loop：依頼を inbox/ に置き、常駐している Claude Code（/loop 1m /inbox）に処理させる（既定）。"
                         "cli：このサーバーが claude -p を起動して処理する")
    args = ap.parse_args()
    srv = make_server(args.host, args.port, os.environ.get("LEARN_TOKEN"), args.runner, tries=PORT_TRIES)
    port = srv.server_address[1]
    if port != args.port and args.port:
        print(f"ポート {args.port} は使用中なので {port} で起動します")
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"learn web UI: http://{shown}:{port}/" + ("?token=..." if os.environ.get("LEARN_TOKEN") else ""))
    if args.runner == "loop":
        print("依頼は inbox/ に置きます。このフォルダで Claude Code を起動し、`/loop 1m /inbox` で処理してください")
    elif not shutil.which(claude_bin()):
        print(f"warning: `{claude_bin()}` が見つかりません。出題・採点には Claude Code が必要です", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
