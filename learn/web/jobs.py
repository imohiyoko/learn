"""Claude Code の実行。--runner cli は claude -p を起動し（Jobs）、--runner loop は依頼を inbox/ に置く（InboxJobs）。
"""
import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from learn import core
from learn.web import files
from learn.web.files import ID_RE, PAST_RE, TAG_RE, now_iso, safe_child
from learn.web.sets import SET_RE


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
        return files.ROOT / "inbox"

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
            return f"{name} {Path(str(inp[key])).as_posix().replace(files.ROOT.as_posix() + '/', '')}"
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
        proc = subprocess.Popen(claude_command(prompt, resume), cwd=files.ROOT, stdin=subprocess.DEVNULL,
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
