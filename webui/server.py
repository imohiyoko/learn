#!/usr/bin/env python3
"""ブラウザから学習を回すためのローカルサーバー。標準ライブラリのみ。

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
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from collections import Counter
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(ROOT))

from learn import analyze, core, route  # noqa: E402

TAG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PAST_RE = re.compile(r"^[a-z0-9]+$")



def configure(root):
    """学習データを置くフォルダを決める（テストでは一時フォルダにする）。"""
    global ROOT, PROFILE, EXAMPLES, ASSETS, LOGS, VIEWABLE, REFS, ASKS, GLOSSARY, GLOSSARY_STATE
    ROOT = Path(root).resolve()
    REFS = ROOT / "refs"
    ASKS = ROOT / "log" / "asks.jsonl"  # 解答中の質問（1質問1行）
    GLOSSARY = ROOT / "log" / "glossary.jsonl"  # 単語帳（1語1行。同じ語は後の行の意味を使う）
    GLOSSARY_STATE = ROOT / "log" / "glossary-state.json"  # 単語帳の「覚えた」と復習の記録
    PROFILE = ROOT / "exam" / "profile.md"
    EXAMPLES = ROOT / "examples"
    ASSETS = ROOT / "questions" / "assets"
    LOGS = {name: (ROOT / "log" / path.name, n) for name, (path, n) in core.LOGS.items()}
    # ブラウザから読めるファイル。rubrics/ は採点前に見せない約束なので出さない
    VIEWABLE = {k: ROOT / k for k in ("questions", "results", "answers")}
    # 問題バンク（bank/keys は正解と採点基準なので、そのままは見せない）・セット・レポート
    global BANK_ITEMS, BANK_KEYS, SETS, REPORTS
    BANK_ITEMS, BANK_KEYS = ROOT / "bank" / "items", ROOT / "bank" / "keys"
    SETS, REPORTS = ROOT / "sets", ROOT / "reports"


configure(ROOT)

# claude -p は対話で許可を求められないので、スキルが使う操作だけを許可しておく。
# ファイルの書き込み（Write / Edit）はここに入れず、acceptEdits に任せる。acceptEdits が
# 承認するのはこのフォルダの中の編集だけなので、外への書き込みは断られる。
ALLOWED_TOOLS = [
    "Read", "Glob", "Grep", "Task", "Agent", "WebSearch", "WebFetch",
    "Bash(python3 scripts/stats.py:*)", "Bash(python3 scripts/analyze.py:*)", "Bash(python3 scripts/route.py:*)",
    "Bash(python scripts/stats.py:*)", "Bash(python scripts/analyze.py:*)", "Bash(python scripts/route.py:*)",
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
    if "<!-- TODO" in text or not core.load_tags(PROFILE):
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


def recommend(kinds, n=3):
    """問題バンクから route.py が次に選ぶ問題の分野を [{tag, name, reason}] で返す（出題と同じ選び方）。
    バンクに問題がなければ空。"""
    tags = core.load_tags(PROFILE)
    if not tags:
        return []
    try:
        # 長文は次の1問だけを出すので、おすすめも1分野。解きかけのセットの問題は、セットを作るときと同じく除く
        r = route.route(ROOT, n=1 if kinds == ("long",) else 10, kinds=kinds,
                        pass_ratio=core.parse_pass_ratio(PROFILE), exclude=busy_items())
    except Exception:  # バンクの読み込みに失敗しても、画面の表示は止めない
        return []
    out, seen = [], set()
    for it in r["items"]:
        t = (it.get("tags") or [None])[0] or (it["concepts"][0].split("/")[0] if it.get("concepts") else None)
        if not t or t in seen:
            continue
        seen.add(t)
        out.append({"tag": t, "name": tags[t][0] if t in tags else t, "reason": it.get("reason") or ""})
        if len(out) >= n:
            break
    return out


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


def list_items():
    """出題済みの問題を新しい順に。採点済みかどうかと点数も付ける。"""
    qdir = VIEWABLE["questions"]
    if not qdir.is_dir():
        return []
    scores = {}
    for name in ("history", "drill"):
        path, _ = LOGS[name]
        for row in core.load_history(path):
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
        meta = core.front_matter(p.read_text(encoding="utf-8"))
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
    tags = core.load_tags(PROFILE)
    return {
        "profile": profile_status(),
        "exam": exam_name(),
        "tags": [{"tag": t, "name": n, "weight": w} for t, (n, w) in tags.items()],
        "examples": list_examples(),
        "recommend": {"written": recommend(("long",)), "drill": recommend(("choice",))},
        "drill_count": drill_default_count(),
        "runner": runner,
        "claude": runner == "loop" or bool(shutil.which(claude_bin())),
        "busy": jobs.busy(),
    }


def analysis():
    tags = core.load_tags(PROFILE)
    if not tags:
        return None
    a = analyze.analyze(tags, analyze.load_rows(LOGS["history"][0], LOGS["drill"][0], LOGS["bank"][0]), core.parse_pass_ratio(PROFILE),
                        asks=analyze.load_asks(ASKS))
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
    if action == "glossary":
        # 単語帳の意味を Claude にまとめて書いてもらう。語は「、」区切りで最大10語
        terms = [s.strip() for s in re.split(r"[、,\n]", str(body.get("terms") or "")) if s.strip()]
        if len(terms) > 10 or any(len(s) > 40 or s.startswith("/") or re.search(r"[\x00-\x1f\x7f]", s) for s in terms):
            raise ValueError("言葉は1つ40字以内、10語までです")
        return " ".join(["/glossary", "、".join(terms)]).strip(), "単語帳" + (f" {'、'.join(terms)}" if terms else "")
    if action == "grade-set":
        sid = str(body.get("id") or "").strip()
        if not SET_RE.match(sid):
            raise ValueError("セットの id が正しくありません")
        return f"/grade-set {sid}", f"セットの採点 {sid}", sid
    if action == "prepare":
        args = [tag] if tag else []
        n = body.get("count")
        if n not in (None, ""):
            n = int(n)
            if not 1 <= n <= 40:
                raise ValueError("作る問題数は1〜40です")
            args.append(str(n))
        return " ".join(["/prepare"] + args), "問題バンクの準備" + (f" {tag}" if tag else "")
    if action == "setup":
        # 勉強したい資格の名前（任意）。1行の短い名前だけ受け付ける
        exam = str(body.get("exam") or "").strip()
        if exam:
            if len(exam) > 60 or exam.startswith("/") or re.search(r"[\x00-\x1f\x7f]", exam):
                raise ValueError("資格の名前は60字以内の1行で書いてください")
            return f"/setup-exam {exam}", f"試験の設定 {exam}"
        return "/setup-exam", "試験の設定"
    raise ValueError("不明な操作です")


GRADE_RE = re.compile(r"^/grade\s+(\S+)")


def grade_target(text):
    """返信が `/grade <id>` なら、その id（採点中の解答の保存を止めるのに使う）"""
    m = GRADE_RE.match(text.strip())
    return m.group(1) if m and ID_RE.match(m.group(1)) else None


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
        self.lock = threading.RLock()  # 採点中の確認と保存・開始を同じロックでまとめるため、入れ子で取れるものにする
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

    def can_reply(self, jid):
        job = self.get(jid)
        with self.lock:
            return bool(job and job.session_id and not any(j.status == "running" for j in self.jobs.values()))

    def reply(self, jid, text):
        job = self.get(jid)
        if not job:
            raise LookupError("見つかりません")
        with self.lock:
            if any(j.status == "running" for j in self.jobs.values()):
                raise RuntimeError("ほかの作業が実行中です。終わってからもう一度どうぞ")
            if not job.session_id:
                raise RuntimeError("この会話は続けられません")
            job.target = grade_target(text) or job.target
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

    MSG_RE = re.compile(r"^(\d{3,})-(user|claude)\.md$")

    def __init__(self):
        self.lock = threading.RLock()  # 採点中の確認と保存・開始を同じロックでまとめるため、入れ子で取れるものにする

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
                fm = core.front_matter(body)
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
        # 一覧の上限より古い依頼も見る
        return any(j["status"] == "running" and j.get("target") == qid for j in self._all())

    def _all(self):
        if not self.base.is_dir():
            return []
        out = [self._read(d) for d in self.base.iterdir() if d.is_dir()]
        out = [o[0] for o in out if o]
        return sorted(out, key=lambda j: j.get("created") or "", reverse=True)

    def list(self):
        return self._all()[:50]

    @staticmethod
    def _write_atomic(p, text):
        # 書きかけのファイルを /inbox に拾われないよう、書き切ってから名前を付ける
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    def _write_user(self, d, n, text):
        self._write_atomic(d / f"{n:03d}-user.md", INBOX_NOTE + "\n" + text.strip() + "\n")

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
            target = grade_target(text)
            if target:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                meta["target"] = target
                self._write_atomic(d / "meta.json", json.dumps(meta, ensure_ascii=False))
            self._write_user(d, r[0]["next"], text)
        return self._read(d)[0]

    def stop(self, jid):
        pass

    def can_reply(self, jid):
        d = self._dir(jid)
        r = self._read(d) if d and d.is_dir() else None
        return bool(r and not r[0]["waiting"])




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


# ---------------------------------------------------------------- 解答中の質問・単語帳

ASK_KINDS = {"word": "言葉の意味", "ask": "何を聞かれているか", "worry": "不安・その他"}
PART_RE = re.compile(r"^設問\d+(\(\d+\))?$")
LOG_LOCK = threading.Lock()


def read_jsonl(path):
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def append_jsonl(path, row):
    with LOG_LOCK:
        path.parent.mkdir(exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def term_key(s):
    """同じ言葉とみなすための形（全角半角・大文字小文字・空白をそろえる）"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))).lower()


def question_tags(qid):
    p = safe_child(VIEWABLE["questions"], qid + ".md")
    if not p or not p.is_file():
        return None
    return re.findall(r"[a-z][a-z0-9_-]*", core.front_matter(p.read_text(encoding="utf-8")).get("tags", ""))


def ask_message(qid, part, kind, text):
    return f"【解答中の質問】{qid}{' ' + part if part else ''}\n種類：{ASK_KINDS[kind]}\n{text}"


def record_ask(qid, part, kind, text):
    tags = question_tags(qid)
    if tags is None:
        raise LookupError("その問題はありません")
    if kind not in ASK_KINDS:
        raise ValueError("質問の種類が正しくありません")
    if part and not PART_RE.match(part):
        raise ValueError("設問の指定が正しくありません")
    if not text or len(text) > 2000:
        raise ValueError("質問は1〜2000字で書いてください")
    append_jsonl(ASKS, {"id": qid, "tags": tags, "part": part or None, "kind": kind, "text": text, "asked_at": now_iso()})


def load_glossary_state():
    try:
        s = json.loads(GLOSSARY_STATE.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def glossary():
    """単語帳を語ごとにまとめる。意味は後の行が優先、分野と出どころは足し合わせる。
    「言葉の意味」の質問で、どの語にも当たらないものは pending として返す（/glossary で意味を作る）"""
    terms = {}
    for row in read_jsonl(GLOSSARY):
        term = str(row.get("term") or "").strip()
        if not term:
            continue
        t = terms.setdefault(term_key(term), {"term": term, "meaning": "", "tags": [], "sources": [], "from": [],
                                              "added_at": row.get("added_at"), "asked": 0})
        t["term"] = term  # 表記は後の行にそろえる
        if row.get("meaning"):
            t["meaning"] = str(row["meaning"])
        for k, v in (("tags", row.get("tags") or []), ("sources", [row.get("source")]), ("from", [row.get("from")])):
            for x in v if isinstance(v, list) else [v]:
                if x and isinstance(x, str) and x not in t[k]:
                    t[k].append(x)
    pending = []
    for a in read_jsonl(ASKS):
        if a.get("kind") != "word":
            continue
        text = term_key(a.get("text", ""))
        hit = [t for k, t in terms.items() if k and k in text]
        for t in hit:
            t["asked"] += 1
        if not hit:
            pending.append({"id": a.get("id"), "text": a.get("text"), "asked_at": a.get("asked_at")})
    state = load_glossary_state()
    out = []
    for k, t in terms.items():
        s = state.get(k) or {}
        t.update({"key": k, "known": bool(s.get("known")), "reviews": s.get("reviews", 0),
                  "misses": s.get("misses", 0), "last_review": s.get("last")})
        out.append(t)
    out.sort(key=lambda t: (t["known"], -t["asked"], -t["misses"], t["term"]))
    return {"terms": out, "pending": pending}


def add_term(term, meaning, tags):
    term, meaning = str(term or "").strip(), str(meaning or "").strip()
    if not term or len(term) > 60 or re.search(r"[\x00-\x1f\x7f]", term):
        raise ValueError("言葉は1〜60字の1行で書いてください")
    if len(meaning) > 2000:
        raise ValueError("意味は2000字以内です")
    tags = [x for x in (tags or []) if isinstance(x, str) and TAG_RE.match(x)][:5]
    append_jsonl(GLOSSARY, {"term": term, "meaning": meaning, "tags": tags, "source": "manual", "from": None,
                            "added_at": now_iso()})


def update_term_state(key, body):
    key = term_key(key or "")
    if not key:
        raise ValueError("言葉がありません")
    with LOG_LOCK:
        state = load_glossary_state()
        s = state.setdefault(key, {})
        if "known" in body:
            s["known"] = bool(body["known"])
        if body.get("review") in ("ok", "again"):
            s["reviews"] = s.get("reviews", 0) + 1
            if body["review"] == "again":
                s["misses"] = s.get("misses", 0) + 1
                s["known"] = False
            else:
                s["known"] = True
            s["last"] = now_iso()
        GLOSSARY_STATE.parent.mkdir(exist_ok=True)
        tmp = GLOSSARY_STATE.with_name(GLOSSARY_STATE.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, GLOSSARY_STATE)
    return s


# ---------------------------------------------------------------- 問題バンクとセット
# セットは scripts/route.py が選んだ問題の組。択一は答えたその場でサーバーが採点し（正解キーと照らすだけ）、
# 短い記述は提出後に /grade-set（Claude）が採点して、解説と分析のレポートを reports/<set>.md に書く。

SET_RE = re.compile(r"^set-\d{8}-\d{6}(-\d+)?$")
CHOICE_RE = re.compile(r"^\s*[-*]\s*([ア-ンA-Z])[\s　.．:：)）]\s*(.+)$")
SET_LOCK = threading.RLock()


def bank_item(iid):
    """問題の本文と選択肢。正解キーは含めない"""
    p = safe_child(BANK_ITEMS, iid + ".md")
    if not p or not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    meta = core.front_matter(text)
    body = core.body_of(text)
    options, lines = [], []
    for line in body.splitlines():
        m = CHOICE_RE.match(line) if meta.get("kind") == "choice" else None
        if m:
            options.append({"label": m.group(1), "text": m.group(2).strip()})
        else:
            lines.append(line)
    return {"id": iid, "kind": meta.get("kind"), "tags": core.parse_list(meta.get("tags")),
            "concepts": core.parse_list(meta.get("concepts")), "difficulty": meta.get("difficulty"),
            "time_min": meta.get("time_min"), "verified": meta.get("verified") == "true",
            "body": "\n".join(lines).strip(), "options": options}


def bank_key(iid):
    p = safe_child(BANK_KEYS, iid + ".md")
    if not p or not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    return {"meta": core.front_matter(text), "body": core.body_of(text)}


def set_dir(sid):
    return safe_child(SETS, sid) if SET_RE.match(sid or "") else None


def load_set(sid):
    d = set_dir(sid)
    try:
        return json.loads((d / "set.json").read_text(encoding="utf-8")) if d else None
    except (OSError, ValueError):
        return None


def save_set(s):
    d = SETS / s["id"]
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "set.json.tmp"
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "set.json")


def concept_names():
    return {c: v["name"] for c, v in route.load_concepts(route.paths(ROOT)).items()}


def bank_status():
    p = route.paths(ROOT)
    if not route.load_concepts(p):
        return {"ready": False, "concepts": 0, "items": 0}
    st = route.stock(ROOT)
    items = route.load_items(p)
    pr = core.parse_pass_ratio(PROFILE)
    return {"ready": bool(items), "concepts": len(st["concepts"]), "items": len(items),
            "kinds": dict(Counter(v["kind"] for v in items.values())), "need": st["total_need"],
            "unverified": len(st["unverified"]), "model": route.model(ROOT, pass_ratio=pr), "stock": st}


SET_KINDS = {"mixed": ("choice", "short"), "choice": ("choice",)}  # 「今日のセット」と、択一だけのドリル


def create_set(n=10, tag=None, kind="mixed"):
    if tag and not TAG_RE.match(tag):
        raise ValueError("タグの形式が正しくありません")
    if kind not in SET_KINDS:
        raise ValueError("セットの種類が正しくありません")
    if not 1 <= n <= 30:
        raise ValueError("問題数は1〜30です")
    with SET_LOCK:  # 同時に作っても、同じ問題が2つのセットに入らないように
        r = route.route(ROOT, n=n, kinds=SET_KINDS[kind], tag=tag or None,
                        pass_ratio=core.parse_pass_ratio(PROFILE), exclude=busy_items())
        if not r["items"]:
            raise RuntimeError("出せる問題がバンクにありません。「問題バンクを作る」で用意してください")
        base = "set-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        sid, k = base, 1
        while (SETS / sid).exists():
            k += 1
            sid = f"{base}-{k}"
        s = {"id": sid, "created": now_iso(), "tag": tag or None, "kind": kind, "want": n, "phase": r["phase"], "status": "open",
             "items": r["items"], "answers": {}, "results": {}, "shortage": r["shortage"]}
        save_set(s)
    return s


def busy_items():
    """解きかけのセットに入っている問題。新しいセットには入れない"""
    busy = set()
    for s in list_sets():
        if s["status"] == "open":
            busy.update(it["id"] for it in (load_set(s["id"]) or {}).get("items", []))
    return busy


def list_sets():
    if not SETS.is_dir():
        return []
    out = []
    for d in SETS.iterdir():
        s = load_set(d.name) if SET_RE.match(d.name) else None
        if not s:
            continue
        res = s.get("results", {})
        out.append({"id": s["id"], "created": s.get("created"), "status": set_status(s), "phase": s.get("phase"),
                    "n": len(s["items"]), "answered": len(s.get("answers", {})),
                    "correct": sum(1 for r in res.values() if r.get("correct")),
                    "choices": sum(1 for it in s["items"] if it["kind"] == "choice"),
                    "report": (REPORTS / f"{s['id']}.md").is_file()})
    return sorted(out, key=lambda x: x["id"], reverse=True)


def set_status(s):
    if (REPORTS / f"{s['id']}.md").is_file():
        return "graded"
    return s.get("status", "open")


def set_view(s):
    """画面に出すセット。正解と解説は、答えた択一と、採点の終わった記述にだけ付ける"""
    status = set_status(s)
    names = concept_names()
    results = dict(s.get("results", {}))
    if status == "graded":  # 記述の点数は /grade-set が log/bank.jsonl に書いたもの
        for r in read_jsonl(LOGS["bank"][0]):
            if r.get("set") == s["id"] and r.get("kind") == "short" and core.is_num(r.get("score")) and core.is_num(r.get("max")) and r["max"] > 0:
                results[r.get("item")] = {"score": r["score"], "max": r["max"], "correct": r["score"] >= r["max"] * 0.6}
    items = []
    for it in s["items"]:
        b = bank_item(it["id"]) or {"id": it["id"], "body": "（問題が見つかりません）", "options": []}
        v = {**it, "body": b["body"], "options": b["options"], "concept_names": [names.get(c, c) for c in it["concepts"]],
             "answer": s.get("answers", {}).get(it["id"]), "result": results.get(it["id"])}
        # 正解が複数ありうるとして確認待ちに戻した問題（verified: false）は、正解を出さない
        if ((it["kind"] == "choice" and v["result"]) or status == "graded") and b.get("verified", True):
            k = bank_key(it["id"])
            if k:
                v["key"] = {"answer": k["meta"].get("answer"), "body": k["body"]}
        items.append(v)
    out = {k: s.get(k) for k in ("id", "created", "tag", "kind", "want", "phase", "shortage")}
    out.update({"status": status, "items": items})
    rp = REPORTS / f"{s['id']}.md"
    if rp.is_file():
        out["report"] = rp.read_text(encoding="utf-8")
        out["compare"] = route.compare(s["id"], ROOT, pass_ratio=core.parse_pass_ratio(PROFILE))
    return out


CONFIDENCES = ("sure", "narrowed", "guess")


def answer_set_item(sid, iid, value, confidence=None):
    with SET_LOCK:
        s = load_set(sid)
        if not s:
            raise LookupError("そのセットはありません")
        if set_status(s) != "open":
            raise RuntimeError("提出済みのセットです")
        it = next((x for x in s["items"] if x["id"] == iid), None)
        if not it:
            raise LookupError("その問題はこのセットにありません")
        value = str(value or "").strip()
        if it["kind"] == "choice":
            if iid in s["results"]:
                raise RuntimeError("この問題はもう答えています")
            b, k = bank_item(iid), bank_key(iid)
            if not k or not b:
                raise LookupError("正解キーがありません")
            if value not in {o["label"] for o in b["options"]}:
                raise ValueError("選択肢から選んでください")
            if confidence not in (None, *CONFIDENCES):
                raise ValueError("確信度が正しくありません")
            correct = value == k["meta"].get("answer", "").strip()
            names = concept_names()
            line = {
                "id": f"{sid}-{iid}", "set": sid, "item": iid, "kind": "choice", "tags": it["tags"] or ["unknown"],
                "concepts": it["concepts"], "difficulty": it["difficulty"], "score": 1 if correct else 0, "max": 1,
                "answer": value, "graded_at": now_iso(),
                "weak": [] if correct else [names.get(c, c) for c in it["concepts"][:1]], "miss_type": {}}
            if confidence:
                line["confidence"] = confidence
            append_jsonl(LOGS["bank"][0], line)
            s["answers"][iid] = value
            s["results"][iid] = {"correct": correct, "graded_at": now_iso()}
            if confidence:
                s["results"][iid]["confidence"] = confidence
        else:
            if len(value) > 4000:
                raise ValueError("解答が長すぎます")
            s["answers"][iid] = value
            d = SETS / sid / "answers"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{iid}.md").write_text(value + "\n", encoding="utf-8")
        save_set(s)
        return s


WORD_RE = re.compile(r"[\x00-\x1f\x7f]")


def ask_set_word(sid, iid, term, again=False):
    """セットを解いている途中の「わからない言葉」。記録して、意味を作るかどうかを返す。
    答える前の択一で、選択肢に出てくる言葉は、意味がそのまま答えになりうるので、答えたあとに回す（deferred）。
    again=True は、答えたあとに回した言葉の意味を作り直すとき（記録はもうあるので足さない）。"""
    term = str(term or "").strip()
    if not term or len(term) > 40 or term.startswith("/") or WORD_RE.search(term) or "、" in term or "," in term:
        raise ValueError("言葉は1つ、40字以内で書いてください")
    s = load_set(sid)
    if not s:
        raise LookupError("そのセットはありません")
    it = next((x for x in s["items"] if x["id"] == iid), None)
    if not it:
        raise LookupError("その問題はこのセットにありません")
    answered = iid in s.get("results", {}) or iid in s.get("answers", {})
    if again:
        if not answered:
            raise RuntimeError("答えてから、もう一度どうぞ")
        return {"deferred": False, "meaning": known_meaning(term_key(term))}
    append_jsonl(ASKS, {"id": iid, "set": sid, "tags": it.get("tags") or [], "concepts": it.get("concepts") or [],
                        "part": None, "kind": "word", "text": term, "asked_at": now_iso()})
    k = term_key(term)
    if it["kind"] == "choice" and not answered:
        b = bank_item(iid) or {"options": []}
        if any(k in term_key(o["text"]) for o in b["options"]):
            return {"deferred": True}
    return {"deferred": False, "meaning": known_meaning(k)}


def known_meaning(key):
    """単語帳にもう意味が入っていれば、その意味（Claude に頼まずに出せる）"""
    return next((t["meaning"] for t in glossary()["terms"] if t["key"] == key and t["meaning"]), None)


def submit_set(sid):
    with SET_LOCK:
        s = load_set(sid)
        if not s:
            raise LookupError("そのセットはありません")
        if set_status(s) != "open":
            raise RuntimeError("提出済みのセットです")
        s["status"] = "submitted"
        s["submitted_at"] = now_iso()
        save_set(s)
        return s


# ---------------------------------------------------------------- 資料（refs/）のアップロード

REF_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md"}
REF_MAX = 50 * 1024 * 1024
REF_DIRS = ("past", "syllabus", "other")
REF_LOCK = threading.Lock()  # 同じ名前の同時アップロードで、先に保存した資料を上書きしないように  # refs/ の下に置くフォルダ（refs/README.md のおすすめに合わせる）


def ref_name(name):
    """アップロードされたファイル名を、refs/<dir>/ の直下に置ける名前にする。だめなら None。"""
    name = (name or "").strip()
    if (not name or len(name) > 120 or name.startswith(".") or "/" in name or "\\" in name
            or re.search(r"[\x00-\x1f\x7f]", name) or Path(name).suffix.lower() not in REF_EXTS):
        return None
    return name


def list_refs():
    if not REFS.is_dir():
        return []
    out = []
    for p in REFS.rglob("*"):
        rel = p.relative_to(REFS)
        if not p.is_file() or rel.as_posix() == "README.md" or any(s.startswith(".") for s in rel.parts):
            continue
        st = p.stat()
        out.append({"path": rel.as_posix(), "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


def save_ref(folder, name, read, size, overwrite=False):
    if folder not in REF_DIRS:
        raise ValueError("置き場所が正しくありません")
    name = ref_name(name)
    if not name:
        raise ValueError("ファイル名が使えません（PDF・画像・テキストだけ。120字以内）")
    if size <= 0:
        raise ValueError("空のファイルです")
    if size > REF_MAX:
        raise ValueError("ファイルが大きすぎます（50MB まで）")
    d = REFS / folder
    d.mkdir(parents=True, exist_ok=True)
    dest = d / name
    if dest.exists() and not overwrite:
        raise FileExistsError(f"refs/{folder}/{name} はすでにあります")
    tmp = d / f".{name}.{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "wb") as f:
            left = size
            while left:
                chunk = read(min(left, 1 << 20))
                if not chunk:
                    raise ValueError("アップロードが途中で切れました")
                f.write(chunk)
                left -= len(chunk)
        with REF_LOCK:
            if dest.exists() and not overwrite:
                raise FileExistsError(f"refs/{folder}/{name} はすでにあります")
            os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return f"{folder}/{name}"


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
                base = VIEWABLE.get(q.get("kind", ""))
                p = safe_child(base, q.get("id", "") + ".md") if base else None
                if not p or not p.is_file():
                    return self.error(404, "ファイルがありません")
                return self.send_json({"id": q["id"], "kind": q["kind"], "text": p.read_text(encoding="utf-8")})
            if path == "/api/profile":
                return self.send_json({"text": PROFILE.read_text(encoding="utf-8") if PROFILE.exists() else None})
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
                s = answer_set_item(m.group(1), str(body.get("item") or ""), body.get("value"),
                                    body.get("confidence") or None)
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
                PROFILE.parent.mkdir(exist_ok=True)
                PROFILE.write_text(text, encoding="utf-8")
                return self.send_json({"ok": True})
            m = re.match(r"^/api/answers/([^/]+)$", path)
            if m:
                qid = m.group(1)
                p = safe_child(VIEWABLE["answers"], qid + ".md")
                if not p or not (VIEWABLE["questions"] / f"{qid}.md").is_file():
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
