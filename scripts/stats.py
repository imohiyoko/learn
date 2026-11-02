#!/usr/bin/env python3
"""成績の集計と、次に出題する分野タグの推薦。標準ライブラリのみ。

使い方:
  python3 scripts/stats.py            分野別の成績とミスの傾向を表示
  python3 scripts/stats.py --next     次に出題すべきタグを1つ
  python3 scripts/stats.py --next 3   上位3つ
  python3 scripts/stats.py --json     集計結果をJSONで出力
  python3 scripts/stats.py --log drill [--next N]
                                      択一ドリルの記録（log/drill.jsonl）で同じことをする
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "exam" / "profile.md"
LOGS = {
    # 名前: (ファイル, 「直近」とみなす件数)。択一は1問ごとの記録なので多めに見る
    "history": (ROOT / "log" / "history.jsonl", 3),
    "drill": (ROOT / "log" / "drill.jsonl", 10),
}
HISTORY, RECENT_N = LOGS["history"]

SUB_TAG_WEIGHT = 0.5      # 副タグの成績は主タグの半分の重みで数える
RECENT_PENALTY = 0.6      # 直前に出たタグは優先度を下げる（連続出題を避ける）
TAG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


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


def aggregate(tags, history, recent_n=RECENT_N):
    stats = {t: {"name": name, "weight": w, "n": 0, "wsum": 0.0, "ratio_sum": 0.0, "ratios": []}
             for t, (name, w) in tags.items()}
    miss = {"knowledge": 0, "reading": 0, "writing": 0}
    for row in history:
        ratio = row["score"] / row["max"]
        for i, tag in enumerate(row["tags"]):
            if tag not in stats:  # profile にないタグも集計はする
                stats[tag] = {"name": "(profile未登録)", "weight": 1.0, "n": 0,
                              "wsum": 0.0, "ratio_sum": 0.0, "ratios": []}
            w = 1.0 if i == 0 else SUB_TAG_WEIGHT
            s = stats[tag]
            s["wsum"] += w
            s["ratio_sum"] += ratio * w
            if i == 0:
                s["n"] += 1
                s["ratios"].append(ratio)
            else:
                s["n_sub"] = s.get("n_sub", 0) + 1
        for k, v in (row.get("miss_type") or {}).items():
            if k in miss:
                miss[k] += int(v)
    for s in stats.values():
        s["avg"] = s["ratio_sum"] / s["wsum"] if s["wsum"] else None
        s["recent"] = (sum(s["ratios"][-recent_n:]) / len(s["ratios"][-recent_n:])) if s["ratios"] else None
    return stats, miss


def recommend(stats, history, k=1):
    last_main = history[-1]["tags"][0] if history else None
    scored = []
    for tag, s in stats.items():
        if s["name"] == "(profile未登録)":
            continue
        if s["wsum"] == 0:
            priority = s["weight"] * 2.0
            reason = "まだ未出題"
        else:
            deficit = 1.0 - (s["recent"] if s["recent"] is not None else s["avg"])
            priority = s["weight"] * (deficit + 0.15 / math.sqrt(1 + s["n"]))
            reason = f"直近平均 {pct(s['recent'] if s['recent'] is not None else s['avg'])}"
        if tag == last_main:
            priority *= RECENT_PENALTY
            reason += "（直前に出題済み）"
        scored.append((priority, tag, s["name"], reason))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:k]


def pct(x):
    return "—" if x is None else f"{round(x * 100)}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--next", nargs="?", const=1, type=int, metavar="N", help="次に出題すべきタグを N 個")
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    ap.add_argument("--log", choices=sorted(LOGS), default="history",
                    help="集計する記録（history：記述式、drill：択一ドリル）")
    args = ap.parse_args()

    tags = load_tags()
    if not tags:
        print("exam/profile.md に分野タグがありません。/setup-exam で設定してください。", file=sys.stderr)
        return 1
    log_path, recent_n = LOGS[args.log]
    history = load_history(log_path)
    stats, miss = aggregate(tags, history, recent_n)

    if args.next is not None:
        if args.next < 1:
            print("--next には1以上を指定してください", file=sys.stderr)
            return 2
        recs = recommend(stats, history, args.next)
        if args.json:
            print(json.dumps([{"tag": t, "name": n, "priority": round(p, 3), "reason": r}
                              for p, t, n, r in recs], ensure_ascii=False, indent=2))
        else:
            for p, t, n, r in recs:
                print(f"{t}\t{n}\t{r}")
        return 0

    if args.json:
        out = {"attempts": len(history), "miss_type": miss,
               "tags": {t: {"name": s["name"], "n": s["n"], "avg": s["avg"], "recent": s["recent"]}
                        for t, s in stats.items()}}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"解答数: {len(history)}")
    if history:
        overall = sum(r["score"] / r["max"] for r in history) / len(history)
        print(f"全体平均: {pct(overall)}")
    print()
    print("回数は「主タグとして出た回数(+副タグとして出た回数)」")
    print(f"{'タグ':<10}{'回数':>7}  {'平均':>5}  {f'直近{recent_n}回':>6}  分野")
    order = sorted(stats.items(), key=lambda kv: (kv[1]["avg"] is None, kv[1]["avg"] or 0))
    for t, s in order:
        count = f"{s['n']}" + (f"(+{s['n_sub']})" if s.get("n_sub") else "")
        print(f"{t:<10}{count:>7}  {pct(s['avg']):>5}  {pct(s['recent']):>6}  {s['name']}")
    total_miss = sum(miss.values())
    if total_miss:
        print()
        print("ミスの種類: " + ", ".join(f"{k} {v}件 ({round(v / total_miss * 100)}%)" for k, v in miss.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
