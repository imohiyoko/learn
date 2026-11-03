"""ファイルと記録の読み込み。スクリプト・web UI が共有する部品。標準ライブラリのみ。

ファイルの形は docs/formats.md を参照。
"""
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "exam" / "profile.md"
LOGS = {
    # 名前: (ファイル, 「直近」とみなす件数)。択一は1問ごとの記録なので多めに見る
    "history": (ROOT / "log" / "history.jsonl", 3),
    "drill": (ROOT / "log" / "drill.jsonl", 10),
    # 問題バンク（scripts/route.py が選ぶセット）の成績。択一と短い記述が混ざる
    "bank": (ROOT / "log" / "bank.jsonl", 10),
}
HISTORY, RECENT_N = LOGS["history"]

TAG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


# ---------------------------------------------------------------- Markdown の front matter

def front_matter(text):
    """先頭の --- で囲まれた front matter を {キー: 文字列} で返す。行末の「# コメント」は除く。"""
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = re.sub(r"\s+#.*$", "", v).strip()
    return meta


def body_of(text):
    return re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=re.S).strip()


def parse_list(v):
    """front matter の [a, b] や「a, b」を ['a', 'b'] にする。"""
    return [x for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9_/.-]*", v or "")]


def parse_time(raw):
    """ISO 8601 の文字列を、タイムゾーン付きの datetime にする。読めなければ None。"""
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


# ---------------------------------------------------------------- exam/profile.md

def load_tags(path=PROFILE):
    """profile.md の「分野タグ」表から {tag: (分野名, 重み)} を読む。"""
    tags = {}
    if not path.exists():
        return tags
    in_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line.strip() == "## 分野タグ"
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not TAG_RE.match(cells[0]):
            continue
        try:
            weight = float(cells[2])
        except ValueError:
            print(f"warning: タグ {cells[0]} の重み「{cells[2]}」が数値でないため 1.0 とします", file=sys.stderr)
            weight = 1.0
        tags[cells[0]] = (cells[1], weight)
    return tags


DEFAULT_PASS = 0.6


def has_section(path, title):
    """profile に「## <title>」の節があるか（穴埋め問題など、使うかどうかを節のあるなしで決めるもの）"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(rf"^##\s*{re.escape(title)}\s*$", text, re.M) is not None


def parse_pass_ratio(path=PROFILE):
    """「合格基準：100点満点中60点以上」「60%以上」などから合格ラインの得点率を読む。"""
    if not path.exists():
        return DEFAULT_PASS
    body = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    for line in body.splitlines():
        # 「- 合格基準：…」の箇条書きだけを見る（本文中の言及は拾わない）
        if not re.match(r"^\s*[-*]\s*合格基準\s*[：:]", line):
            continue
        text = line
        m = re.search(r"(\d+(?:\.\d+)?)\s*点満点中\s*(\d+(?:\.\d+)?)\s*点", text)
        if m and float(m.group(1)) > 0:
            return min(max(float(m.group(2)) / float(m.group(1)), 0.0), 1.0)
        m = re.search(r"(\d+(?:\.\d+)?)\s*[%％]", text)
        if m:
            return min(max(float(m.group(1)) / 100, 0.0), 1.0)
        m = re.search(r"(\d+(?:\.\d+)?)\s*割", text)
        if m:
            return min(max(float(m.group(1)) / 10, 0.0), 1.0)
    return DEFAULT_PASS


# ---------------------------------------------------------------- 記録（log/*.jsonl）

def load_history(path=HISTORY):
    rows = []
    if not path.exists():
        return rows
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            print(f"warning: log/{path.name} {n}行目がJSONとして読めません。スキップします", file=sys.stderr)
            continue
        problem = validate(row)
        if problem:
            print(f"warning: log/{path.name} {n}行目をスキップ（{problem}）", file=sys.stderr)
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("graded_at") or ""))
    return rows


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_finite(x):
    """float にしても有限な数値か（NaN・無限大・float に収まらない巨大な整数を除く）。"""
    if not is_num(x):
        return False
    try:
        return math.isfinite(float(x))
    except OverflowError:
        return False


def validate(row):
    """記録1行の形をチェックし、問題があれば理由を返す。"""
    if not isinstance(row, dict):
        return "オブジェクトではない"
    if not is_finite(row.get("score")):
        return "score が数値でない"
    if not is_finite(row.get("max")) or row["max"] <= 0:
        return "max が正の数値でない"
    tags = row.get("tags")
    if not isinstance(tags, list) or not tags or not all(isinstance(t, str) and t for t in tags):
        return "tags が文字列のリストでない"
    mt = row.get("miss_type")
    if mt is not None and (not isinstance(mt, dict) or not all(is_finite(v) for v in mt.values())):
        return "miss_type の値が数値でない"
    weak = row.get("weak")
    if weak is not None and not isinstance(weak, list):
        return "weak がリストでない"
    return None
