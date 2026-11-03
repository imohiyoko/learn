"""web UI が学習データを置くフォルダと、ファイルの読み書きの部品。

configure() でフォルダを決める（テストでは一時フォルダ）。ほかのモジュールは files.ROOT のように
モジュールの属性として読む（configure で差し替わるので、from で取り込まない）。
"""
import json
import re
import threading
from datetime import datetime
from pathlib import Path

from learn import core


ROOT = Path(__file__).resolve().parents[2]

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


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def safe_child(base, name):
    """base 直下のファイルだけを返す（../ などで外に出させない）。"""
    if not ID_RE.match(name):
        return None
    p = (base / name).resolve()
    return p if p.parent == base.resolve() else None
