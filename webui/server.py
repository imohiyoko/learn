#!/usr/bin/env python3
"""ブラウザから学習を回すためのローカルサーバー。標準ライブラリのみ。

出題・採点などの中身は、これまでどおり Claude Code のスキル（.claude/skills/）が行う。
このサーバーは、ブラウザで選んだ内容を Claude Code に渡し、返事をブラウザに見せるだけ。
学習データはこれまでと同じファイル（questions/ log/ など）に残る。渡し方は2つ：

  --runner loop（既定）  依頼を inbox/ に置く。このフォルダで動かしている Claude Code が
                         `/loop 1m /inbox` で拾って処理し、返事を inbox/ に書く
  --runner cli           このサーバーが `claude -p "/question web"` を起動して処理する
                         （Docker など、常駐の Claude Code がないとき）

使い方:
  python3 webui/server.py                 http://127.0.0.1:8765 で起動
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
import hmac
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(ROOT / "scripts"))

import analyze  # noqa: E402
import stats  # noqa: E402

TAG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PAST_RE = re.compile(r"^[a-z0-9]+$")



def configure(root):
    """学習データを置くフォルダを決める（テストでは一時フォルダにする）。"""
    global ROOT, PROFILE, EXAMPLES, ASSETS, LOGS, VIEWABLE
    ROOT = Path(root).resolve()
    PROFILE = ROOT / "exam" / "profile.md"
    EXAMPLES = ROOT / "examples"
    ASSETS = ROOT / "questions" / "assets"
    LOGS = {name: (ROOT / "log" / path.name, n) for name, (path, n) in stats.LOGS.items()}
    # ブラウザから読めるファイル。rubrics/ は採点前に見せない約束なので出さない
    VIEWABLE = {k: ROOT / k for k in ("questions", "results", "answers")}


configure(ROOT)

# claude -p は対話で許可を求められないので、スキルが使う操作だけを許可しておく。
# ファイルの書き込み（Write / Edit）はここに入れず、acceptEdits に任せる。acceptEdits が
# 承認するのはこのフォルダの中の編集だけなので、外への書き込みは断られる。
ALLOWED_TOOLS = [
    "Read", "Glob", "Grep", "Task", "Agent", "WebSearch", "WebFetch",
    "Bash(python3 scripts/stats.py:*)", "Bash(python3 scripts/analyze.py:*)",
    "Bash(python scripts/stats.py:*)", "Bash(python scripts/analyze.py:*)",
    "Bash(date:*)", "Bash(mkdir:*)", "Bash(ls:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)",
    "Bash(pdftoppm:*)", "Bash(pdfinfo:*)",
]

SYSTEM_NOTE = """学習者はブラウザの画面（webui/）から操作している。
- 学習者はブラウザで questions/<id>.md と results/<id>.md を読み、answers/<id>.md をブラウザで書いて保存する。
- 問題を出したら、本文はブラウザで表示されるので、チャットには要点と <id> と「解答欄に書いて『採点する』を押してください」という案内だけを書けばよい。
- 択一ドリルの解答（例：1ア 2ウ）は、このチャットの返信として届く。
- 確認が必要なときは、質問を1つずつ短く書く（学習者はこのチャットに返信できる）。"""


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 状態の読み出し

def profile_status():
    if not PROFILE.exists():
        return "missing"
    text = PROFILE.read_text(encoding="utf-8")
    if "<!-- TODO" in text or not stats.load_tags(PROFILE):
        return "todo"
    return "ok"


def exam_name():
    if not PROFILE.exists():
        return None
    m = re.search(r"^- 名称：(.+)$", PROFILE.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else None


def drill_default_count():
    if PROFILE.exists():
        m = re.search(r"^- 1回の問題数：\s*(\d+)", PROFILE.read_text(encoding="utf-8"), re.M)
        if m:
            return int(m.group(1))
    return 10


def recommend(log, n=3):
    """stats.py --next と同じ推薦を [{tag, name, reason}] で返す。"""
    tags = stats.load_tags(PROFILE)
    if not tags:
        return []
    path, recent_n = LOGS[log]
    history = stats.load_history(path)
    agg, _ = stats.aggregate(tags, history, recent_n)
    return [{"tag": t, "name": nm, "reason": r} for _, t, nm, r in stats.recommend(agg, history, n)]


def list_examples():
    if not EXAMPLES.is_dir():
        return []
    out = []
    for d in sorted(EXAMPLES.iterdir()):
        p = d / "profile.md"
        if p.is_file():
            m = re.search(r"^- 名称：(.+)$", p.read_text(encoding="utf-8"), re.M)
            out.append({"key": d.name, "name": m.group(1).strip() if m else d.name})
    return out


def front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta


def list_items():
    """出題済みの問題を新しい順に。採点済みかどうかと点数も付ける。"""
    qdir = VIEWABLE["questions"]
    if not qdir.is_dir():
        return []
    scores = {}
    for name in ("history", "drill"):
        path, _ = LOGS[name]
        for row in stats.load_history(path):
            rid = str(row.get("id", ""))
            if name == "history":
                # 同じ問題を採点し直したら、最新の1行だけを使う（load_history は時刻順）
                scores[rid] = [row.get("score", 0), row.get("max", 0)]
                continue
            s = scores.setdefault(re.sub(r"-q\d+$", "", rid), [0, 0])
            s[0] += row.get("score", 0)
            s[1] += row.get("max", 0)
    items = []
    for p in qdir.glob("*.md"):
        qid = p.stem
        meta = front_matter(p.read_text(encoding="utf-8"))
        items.append({
            "id": qid,
            "kind": "drill" if qid.startswith("drill-") else "written",
            "tags": meta.get("tags", ""),
            "mtime": p.stat().st_mtime,
            "graded": (VIEWABLE["results"] / f"{qid}.md").exists(),
            "has_answer": (VIEWABLE["answers"] / f"{qid}.md").exists(),
            "score": scores.get(qid),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def state(jobs, runner):
    tags = stats.load_tags(PROFILE)
    return {
        "profile": profile_status(),
        "exam": exam_name(),
        "tags": [{"tag": t, "name": n, "weight": w} for t, (n, w) in tags.items()],
        "examples": list_examples(),
        "recommend": {"written": recommend("history"), "drill": recommend("drill")},
        "drill_count": drill_default_count(),
        "runner": runner,
        "claude": runner == "loop" or bool(shutil.which(claude_bin())),
        "busy": jobs.busy(),
    }


def analysis():
    tags = stats.load_tags(PROFILE)
    if not tags:
        return None
    a = analyze.analyze(tags, analyze.load_rows(LOGS["history"][0], LOGS["drill"][0]), analyze.parse_pass_ratio(PROFILE))
    # 画面用に、analyze.py が文字の表示に使うラベルを添える
    for t in a["tags"].values():
        t["gap_label"] = analyze.GAP_LABEL.get(t.get("gap"))
        for kind in ("written", "drill"):
            if t.get(kind):
                t[kind]["trend_label"] = analyze.TREND_LABEL.get(t[kind].get("trend"), "").strip()
    return a


# ---------------------------------------------------------------- claude の実行

def claude_bin():
    return os.environ.get("LEARN_CLAUDE", "claude")


def build_prompt(body):
    """ブラウザで選んだ内容をスラッシュコマンドにする。自由入力は受け付けない。"""
    action = body.get("action")
    tag = str(body.get("tag") or "").strip()
    if tag and not TAG_RE.match(tag):
        raise ValueError("タグの形式が正しくありません")
    if action == "question":
        past = str(body.get("past") or "").strip()
        if past:
            if not re.match(r"^past-[a-z0-9]+-[a-z0-9]+$", past):
                raise ValueError("過去問の id は past-<年度>-<問番号> の形です")
            return f"/question {past}", f"記述式 {past}"
        return f"/question {tag}".strip(), f"記述式 {tag or '（おまかせ）'}"
    if action == "drill":
        n = body.get("count")
        args = [tag] if tag else []
        if n not in (None, ""):
            n = int(n)
            if not 1 <= n <= 50:
                raise ValueError("問題数は1〜50です")
            args.append(str(n))
        return " ".join(["/drill"] + args), f"択一 {tag or '（おまかせ）'}" + (f" {n}問" if n else "")
    if action == "grade":
        qid = str(body.get("id") or "").strip()
        if not ID_RE.match(qid) or qid.startswith("drill-"):
            raise ValueError("採点する問題の id が正しくありません")
        return f"/grade {qid}", f"採点 {qid}", qid
    if action == "review":
        return "/review", "振り返り"
    if action == "import":
        year, q = str(body.get("year") or "").strip(), str(body.get("q") or "").strip()
        if not (PAST_RE.match(year) and PAST_RE.match(q)):
            raise ValueError("年度と問番号は英小文字と数字で（例：r06a q1）")
        return f"/import-past {year} {q}", f"過去問の取り込み {year} {q}"
    if action == "setup":
        return "/setup-exam", "試験の設定"
    raise ValueError("不明な操作です")


class Job:
    def __init__(self, title, target=None):
        self.target = target
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.created = now_iso()
        self.session_id = None
        self.status = "running"
        self.events = []
        self.question_ids = []
        self.cond = threading.Condition()
        self.proc = None

    def add(self, ev):
        with self.cond:
            ev["seq"] = len(self.events)
            self.events.append(ev)
            self.cond.notify_all()

    def finish(self, status):
        # 状態の更新と end を同じロックの中で行い、見る側が途中の状態を読まないようにする
        with self.cond:
            self.status = status
            self.add({"type": "end"})

    def summary(self):
        return {"id": self.id, "title": self.title, "created": self.created, "status": self.status,
                "question_ids": self.question_ids, "target": self.target, "can_reply": bool(self.session_id) and self.status != "running"}


class Jobs:
    KEEP = 30

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.order = []

    def busy(self):
        with self.lock:
            return any(j.status == "running" for j in self.jobs.values())

    def get(self, jid):
        with self.lock:
            return self.jobs.get(jid)

    def list(self):
        with self.lock:
            return [self.jobs[i].summary() for i in reversed(self.order)]

    def start(self, prompt, title, target=None):
        with self.lock:
            if any(j.status == "running" for j in self.jobs.values()):
                raise RuntimeError("ほかの作業が実行中です。終わってからもう一度どうぞ")
            job = Job(title, target)
            self.jobs[job.id] = job
            self.order.append(job.id)
            # 古い会話は捨てる（メモリを使い続けないように）
            while len(self.order) > self.KEEP:
                del self.jobs[self.order.pop(0)]
        job.add({"type": "user", "text": prompt})
        threading.Thread(target=run_claude, args=(job, prompt), daemon=True).start()
        return job

    def grading(self, qid):
        with self.lock:
            return any(j.status == "running" and j.target == qid for j in self.jobs.values())

    def events(self, jid, after, wait):
        job = self.get(jid)
        if not job:
            return None
        with job.cond:
            if len(job.events) <= after and wait:
                job.cond.wait(timeout=wait)
            return job.summary(), job.events[after:]

    def stop(self, jid):
        job = self.get(jid)
        if job and job.proc and job.proc.poll() is None:
            job.proc.terminate()

    def reply(self, jid, text):
        job = self.get(jid)
        if not job:
            raise LookupError("見つかりません")
        with self.lock:
            if any(j.status == "running" for j in self.jobs.values()):
                raise RuntimeError("ほかの作業が実行中です。終わってからもう一度どうぞ")
            if not job.session_id:
                raise RuntimeError("この会話は続けられません")
            with job.cond:
                job.status = "running"
                job.add({"type": "user", "text": text})
        threading.Thread(target=run_claude, args=(job, text, job.session_id), daemon=True).start()
        return job.summary()


INBOX_NOTE = """<!-- learn web UI からの依頼。Claude Code の /inbox スキルが処理して、同じフォルダに NNN-claude.md を書く -->
"""


class InboxJobs:
    """claude を自分では起動せず、依頼を inbox/ に置くだけのやり方。

    PC で常駐している Claude Code（例：`/loop 1m /inbox`）が inbox/ を見て処理し、
    返事を同じフォルダに書く。会話は1つのフォルダ＝1つの依頼：

      inbox/<job>/meta.json        画面での名前など
      inbox/<job>/000-user.md      最初の依頼（/drill web 5 など）
      inbox/<job>/000-claude.md    Claude の返事（front matter に questions: [<id>]）
      inbox/<job>/001-user.md      学習者の返信（1ア 2ウ … など）
      ...
    """

    MSG_RE = re.compile(r"^(\d{3})-(user|claude)\.md$")

    def __init__(self):
        self.lock = threading.Lock()

    @property
    def base(self):
        return ROOT / "inbox"

    def _dir(self, jid):
        return safe_child(self.base, jid) if re.match(r"^[0-9a-z-]+$", jid or "") else None

    def _read(self, d):
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        msgs = {}
        for p in d.iterdir():
            m = self.MSG_RE.match(p.name)
            if m:
                msgs.setdefault(int(m.group(1)), {})[m.group(2)] = p
        events, qids, pending = [], [], False
        for n in sorted(msgs):
            pair = msgs[n]
            if "user" in pair:
                text = pair["user"].read_text(encoding="utf-8").replace(INBOX_NOTE, "").strip()
                events.append({"type": "user", "text": text})
            if "claude" in pair:
                body = pair["claude"].read_text(encoding="utf-8")
                fm = front_matter(body)
                for q in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", fm.get("questions", "")):
                    if q not in qids and ID_RE.match(q):
                        qids.append(q)
                text = re.sub(r"^---\n.*?\n---\n", "", body, count=1, flags=re.S).strip()
                events.append({"type": "text", "text": text})
                events.append({"type": "end"})
            elif "user" in pair:
                pending = True
        for i, e in enumerate(events):
            e["seq"] = i
        last = max(msgs) if msgs else -1
        summary = {"id": d.name, "title": meta.get("title", d.name), "created": meta.get("created"),
                   "status": "running" if pending else "idle", "question_ids": qids,
                   "target": meta.get("target"), "can_reply": not pending, "waiting": pending, "next": last + 1}
        return summary, events

    def busy(self):
        return False  # 依頼は順番に並ぶので、いくつ置いてもよい

    def grading(self, qid):
        return any(j["status"] == "running" and j.get("target") == qid for j in self.list())

    def list(self):
        if not self.base.is_dir():
            return []
        out = [self._read(d) for d in self.base.iterdir() if d.is_dir()]
        out = [o[0] for o in out if o]
        return sorted(out, key=lambda j: j.get("created") or "", reverse=True)[:50]

    def _write_user(self, d, n, text):
        (d / f"{n:03d}-user.md").write_text(INBOX_NOTE + "\n" + text.strip() + "\n", encoding="utf-8")

    def start(self, prompt, title, target=None):
        with self.lock:
            jid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
            d = self.base / jid
            d.mkdir(parents=True)
            (d / "meta.json").write_text(json.dumps({"title": title, "created": now_iso(), "target": target},
                                                    ensure_ascii=False), encoding="utf-8")
            self._write_user(d, 0, prompt)
        return self._read(d)[0]

    def events(self, jid, after, wait):
        d = self._dir(jid)
        if not d or not d.is_dir():
            return None
        deadline = time.time() + (wait or 0)
        while True:
            r = self._read(d)
            if r is None:
                return None
            if len(r[1]) > after or time.time() >= deadline:
                return r[0], r[1][after:]
            time.sleep(1)

    def reply(self, jid, text):
        d = self._dir(jid)
        with self.lock:
            r = self._read(d) if d and d.is_dir() else None
            if not r:
                raise LookupError("見つかりません")
            if r[0]["waiting"]:
                raise RuntimeError("まだ前の依頼の返事が来ていません")
            self._write_user(d, r[0]["next"], text)
        return self._read(d)[0]

    def stop(self, jid):
        pass




def claude_command(prompt, resume=None):
    cmd = [claude_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS,
           "--append-system-prompt", SYSTEM_NOTE]
    if resume:
        cmd += ["--resume", resume]
    cmd += shlex.split(os.environ.get("LEARN_CLAUDE_ARGS", ""))
    return cmd


def describe_tool(name, inp):
    """ツール呼び出しを1行にする。書き込む中身（採点基準など）は出さない。"""
    if not isinstance(inp, dict):
        return name
    for key in ("file_path", "path", "pattern"):
        if inp.get(key):
            return f"{name} {Path(str(inp[key])).as_posix().replace(ROOT.as_posix() + '/', '')}"
    if name == "Bash" and inp.get("command"):
        cmd = str(inp["command"])
        if "rubrics" in cmd:  # 採点基準を書き込むコマンドの中身は出さない
            return "$ （採点基準の操作）"
        first = cmd.splitlines()[0] if cmd.strip() else ""
        return f"$ {first[:160]}" + (" …" if len(first) > 160 or "\n" in cmd else "")
    # Task / Agent の description などは、正解に触れることがあるので出さない
    return name


def handle_message(job, msg):
    t = msg.get("type")
    if msg.get("session_id"):
        job.session_id = msg["session_id"]
    if msg.get("parent_tool_use_id"):
        # サブエージェント（verifier・grader）の出力は流さない。verifier の解答は答えそのものなので
        return
    if t == "assistant":
        for block in (msg.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                job.add({"type": "text", "text": block["text"]})
            elif block.get("type") == "tool_use":
                inp = block.get("input") or {}
                job.add({"type": "tool", "text": describe_tool(block.get("name", ""), inp)})
                fp = str(inp.get("file_path") or "")
                m = re.search(r"(?:^|/)questions/([^/]+)\.md$", fp)
                if m and block.get("name") in ("Write", "Edit") and m.group(1) not in job.question_ids:
                    job.question_ids.append(m.group(1))
    elif t == "result":
        if msg.get("is_error"):
            job.add({"type": "error", "text": str(msg.get("result") or "エラーで終了しました")})
        job.add({"type": "done", "cost": msg.get("total_cost_usd"), "seconds": (msg.get("duration_ms") or 0) / 1000})


def run_claude(job, prompt, resume=None):
    try:
        _run_claude(job, prompt, resume)
    except Exception as e:  # どんな失敗でも「実行中」のまま残さない
        proc = job.proc
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait()
        job.add({"type": "error", "text": f"{type(e).__name__}: {e}"})
        job.finish("error")


def _run_claude(job, prompt, resume=None):
    try:
        proc = subprocess.Popen(claude_command(prompt, resume), cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    except FileNotFoundError:
        job.add({"type": "error", "text": f"`{claude_bin()}` が見つかりません。Claude Code をインストールしてログインしてください"})
        job.finish("error")
        return
    job.proc = proc
    err = []
    # stderr は別スレッドで読む（溜まると claude が止まるため）
    t = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    t.start()
    with proc:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                msg = None
            if isinstance(msg, dict):
                handle_message(job, msg)
            else:
                job.add({"type": "text", "text": line})
        t.join()
        code = proc.wait()
    if code != 0:
        msg = "".join(err).strip() or f"claude が終了コード {code} で終わりました"
        job.add({"type": "error", "text": msg[-2000:]})
    job.finish("error" if code != 0 else "idle")


# ---------------------------------------------------------------- HTTP

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def safe_child(base, name):
    """base 直下のファイルだけを返す（../ などで外に出させない）。"""
    if not ID_RE.match(name):
        return None
    p = (base / name).resolve()
    return p if p.parent == base.resolve() else None


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

    def authed(self):
        if not self.token:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        given = cookie["learn_token"].value if "learn_token" in cookie else ""
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
            self.send_header("Set-Cookie", f"learn_token={self.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
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
                base = VIEWABLE.get(q.get("kind", ""))
                p = safe_child(base, q.get("id", "") + ".md") if base else None
                if not p or not p.is_file():
                    return self.error(404, "ファイルがありません")
                return self.send_json({"id": q["id"], "kind": q["kind"], "text": p.read_text(encoding="utf-8")})
            if path == "/api/profile":
                return self.send_json({"text": PROFILE.read_text(encoding="utf-8") if PROFILE.exists() else None})
            if path == "/api/jobs":
                return self.send_json({"jobs": self.jobs.list()})
            m = re.match(r"^/api/jobs/([0-9a-z-]+)$", path)
            if m:
                r = self.jobs.events(m.group(1), int(q.get("after", 0)), min(float(q.get("wait", 0) or 0), 30))
                if not r:
                    return self.error(404, "見つかりません")
                return self.send_json({"job": r[0], "events": r[1]})
            if path.startswith("/assets/"):
                p = safe_child(ASSETS, path[len("/assets/"):])
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
        path = urlparse(self.path).path
        try:
            body = self.body()
            if path == "/api/run":
                prompt, title, *target = build_prompt(body)
                if body.get("action") != "setup" and profile_status() != "ok":
                    return self.error(409, "先に試験の設定をしてください")
                job = self.jobs.start(prompt, title, *target)
                return self.send_json({"job": job if isinstance(job, dict) else job.summary()})
            m = re.match(r"^/api/jobs/([0-9a-z-]+)/reply$", path)
            if m:
                text = str(body.get("text") or "").strip()
                if not text:
                    return self.error(400, "空です")
                return self.send_json({"job": self.jobs.reply(m.group(1), text[:20000])})
            m = re.match(r"^/api/jobs/([0-9a-z-]+)/stop$", path)
            if m:
                self.jobs.stop(m.group(1))
                return self.send_json({"ok": True})
            if path == "/api/setup-example":
                key = str(body.get("key") or "")
                src = safe_child(EXAMPLES, key)
                if not src or not (src / "profile.md").is_file():
                    return self.error(404, "その例はありません")
                if PROFILE.exists() and not body.get("overwrite"):
                    return self.error(409, "exam/profile.md がすでにあります")
                PROFILE.parent.mkdir(exist_ok=True)
                shutil.copyfile(src / "profile.md", PROFILE)
                return self.send_json({"ok": True})
        except LookupError as e:
            return self.error(404, str(e))
        except (ValueError, RuntimeError) as e:
            return self.error(400 if isinstance(e, ValueError) else 409, str(e))
        except Exception as e:
            return self.error(500, f"{type(e).__name__}: {e}")
        return self.error(404, "not found")

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
                PROFILE.parent.mkdir(exist_ok=True)
                PROFILE.write_text(text, encoding="utf-8")
                return self.send_json({"ok": True})
            m = re.match(r"^/api/answers/([^/]+)$", path)
            if m:
                qid = m.group(1)
                p = safe_child(VIEWABLE["answers"], qid + ".md")
                if not p or not (VIEWABLE["questions"] / f"{qid}.md").is_file():
                    return self.error(404, "その問題はありません")
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


class Server6(Server):
    address_family = socket.AF_INET6


def make_server(host="127.0.0.1", port=8765, token=None, allow_no_token=False, runner="loop"):
    if runner not in RUNNERS:
        raise SystemExit(f"--runner は {' / '.join(RUNNERS)} のどれかです")
    if host not in LOOPBACK and not token and not allow_no_token:
        raise SystemExit("127.0.0.1 以外で待ち受けるときは LEARN_TOKEN を設定してください"
                         "（ポートを 127.0.0.1 にしか出さない Docker などでは --allow-no-token）")
    # 設定はサーバーごとに持つ（同じプロセスで複数作っても混ざらないように）
    handler = type("BoundHandler", (Handler,), {
        "token": token or None,
        "runner": runner,
        "jobs": RUNNERS[runner](),
        # トークンなしなら、Host が localhost のリクエストだけを受ける。127.0.0.1 で待ち受けるときの
        # DNS rebinding 対策と、--allow-no-token のままポートを外に出してしまったときの歯止めを兼ねる
        "allowed_hosts": LOOPBACK if (host in LOOPBACK or not token) else None,
    })
    cls = Server6 if ":" in host else Server
    return cls((host, port), handler)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("LEARN_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LEARN_PORT", "8765")))
    ap.add_argument("--allow-no-token", action="store_true",
                    help="127.0.0.1 以外でもトークンなしで待ち受ける（外から届かない環境だけで使う）")
    ap.add_argument("--runner", choices=sorted(RUNNERS), default=os.environ.get("LEARN_RUNNER", "loop"),
                    help="loop：依頼を inbox/ に置き、常駐している Claude Code（/loop 1m /inbox）に処理させる（既定）。"
                         "cli：このサーバーが claude -p を起動して処理する")
    args = ap.parse_args()
    srv = make_server(args.host, args.port, os.environ.get("LEARN_TOKEN"), args.allow_no_token, args.runner)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"learn web UI: http://{shown}:{args.port}/" + ("?token=..." if os.environ.get("LEARN_TOKEN") else ""))
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
