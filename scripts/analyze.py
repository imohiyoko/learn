#!/usr/bin/env python3
"""得意・不得意の分析。記述式（log/history.jsonl）と択一（log/drill.jsonl）を合わせて、
分野タグごとに「どのくらいできるか」「どのくらい確かか」「伸びているか」「なぜ落とすか」を出す。
標準ライブラリのみ。

使い方:
  python3 scripts/analyze.py           分析結果を表示
  python3 scripts/analyze.py --json    同じ内容をJSONで出力

考え方（imohiyoko/learning の学習者モデル ADR 0018 を、1人・ファイルだけで回せる形に縮めたもの）:
- 分野ごとの力 θ を、記述式と択一で別々に、1解答（択一は1問）ずつ Laplace 近似の Elo で更新する。
  見込み p = σ(θ)。記述式は得点率、択一は正誤(1/0)を観測 y とする。
  択一は1問ずつ数が多く、記述式とは難しさも違うので、混ぜると択一に引っ張られるため分けている
  重みは主タグ 1、副タグ 0.5
- 不確かさ v も一緒に持ち、p の 95% の幅を出す。幅が合格ラインをまたがなければ「確か」
- 分野の判定は、記録が足りている形式のうち見込みの低い方で行う（記述で落とす分野を
  択一の正答率で「得意」と見誤らないため）
- 合格ラインは exam/profile.md の「合格基準」から読む（読めなければ 60%）
"""
import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats  # noqa: E402  同じフォルダの stats.py（タグとログの読み込みを共有する）

DEFAULT_PASS = 0.6
SUB_TAG_WEIGHT = 0.5
V_INIT = 1.0                 # 観測前の不確かさ
V_MIN = 0.05                 # 不確かさの下限（古い結果に縛られすぎないように）
MIN_EVIDENCE = {"written": 3.0, "drill": 5.0}  # 形式ごとに、重みの合計がこれ未満なら判定に使わない
STRONG_MARGIN = 0.15         # 合格ライン + これ以上で「得意」
TREND_MIN = 4                # 伸びを見るのに必要な主タグでの回数
TREND_DELTA = 0.1            # 前半と後半の差がこれ以上なら上昇/下降

MISS_ADVICE = {
    "knowledge": "用語・仕組みを参考書で確認し、/drill で知識を固める",
    "reading": "設問を先に読み、本文の根拠の箇所を探してから書く",
    "writing": "解答例と要素の数を比べ、字数の8割以上を埋める",
}


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def parse_pass_ratio(path=stats.PROFILE):
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


def load_rows(history_path=None, drill_path=None):
    """両方のログを読み、kind（written / drill）を付けて時刻順に並べる。"""
    history_path = history_path or stats.LOGS["history"][0]
    drill_path = drill_path or stats.LOGS["drill"][0]
    rows = []
    for kind, path in (("written", history_path), ("drill", drill_path)):
        for r in stats.load_history(path):
            rows.append(dict(r, _kind=kind))
    rows.sort(key=sort_key)
    return rows


def sort_key(row):
    """graded_at を時刻として並べる（タイムゾーンの違う記録が混ざっても順序を保つ）。"""
    raw = str(row.get("graded_at") or "")
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.astimezone()  # タイムゾーンのない値は、その日付での端末の時刻とみなす（夏時間も考慮）
        return (0, t.timestamp(), raw)
    except ValueError:
        return (1, 0.0, raw)


def new_model():
    return {"theta": 0.0, "v": V_INIT, "evidence": 0.0, "series": [], "n_sub": 0}


def new_tag(name="(profile未登録)", weight=1.0):
    return {"name": name, "weight": weight, "models": {"written": new_model(), "drill": new_model()},
            "miss": Counter(), "weak": Counter()}


def update(m, y, w):
    """Laplace 近似の Elo（ADR 0018 の 5.1 と同じ式、b = 0）。"""
    p = sigmoid(m["theta"])
    info = w * m["v"] * p * (1 - p)
    m["theta"] += w * m["v"] * (y - p) / (1 + info)
    m["v"] = max(m["v"] / (1 + info), V_MIN)
    m["evidence"] += w


def summarize(m, kind):
    if m["evidence"] == 0:
        return None
    sd = math.sqrt(m["v"])
    return {
        "p": sigmoid(m["theta"]),
        "low": sigmoid(m["theta"] - 1.96 * sd),
        "high": sigmoid(m["theta"] + 1.96 * sd),
        "n": len(m["series"]),
        "n_sub": m["n_sub"],
        "avg": avg(m["series"]),
        "evidence": round(m["evidence"], 2),
        "enough": m["evidence"] >= MIN_EVIDENCE[kind],
        "trend": trend(m["series"]),
    }


def analyze(tags, rows, pass_ratio=DEFAULT_PASS):
    per = {t: new_tag(name, w) for t, (name, w) in tags.items()}
    total_miss = Counter()
    weak_all = defaultdict(lambda: {"count": 0, "tags": Counter(), "label": None})

    for row in rows:
        y = ratio(row)
        row_tags = list(dict.fromkeys(row["tags"]))  # 同じタグの重複は1つにまとめる
        for i, tag in enumerate(row_tags):
            s = per.setdefault(tag, new_tag())
            m = s["models"][row["_kind"]]
            update(m, y, 1.0 if i == 0 else SUB_TAG_WEIGHT)
            if i == 0:
                m["series"].append(y)
                for k, v in (row.get("miss_type") or {}).items():
                    n = int(v) if stats.is_num(v) else 0
                    if n > 0:
                        s["miss"][k] += n
                        total_miss[k] += n
            else:
                m["n_sub"] += 1
        seen = set()
        for term in row.get("weak") or []:
            if not isinstance(term, str) or not term.strip():
                continue
            key = re.sub(r"\s+", "", term).casefold()
            if key in seen:  # 1つの記録の中の重複は1回に数える
                continue
            seen.add(key)
            e = weak_all[key]
            e["count"] += 1
            e["label"] = e["label"] or term.strip()
            # 弱点はその問題の主題（主タグ）のものとして数える
            e["tags"][row_tags[0]] += 1
            per[row_tags[0]]["weak"][key] += 1

    result = {}
    for tag, s in per.items():
        fm = {k: summarize(m, k) for k, m in s["models"].items()}
        decided = [(k, f) for k, f in fm.items() if f and f["enough"]]
        if not any(fm.values()):
            status, basis = "未出題", None
        elif not decided:
            status, basis = "測定中", None
        else:
            basis, f = min(decided, key=lambda kf: kf[1]["p"])
            if f["p"] < pass_ratio:
                status = "苦手"
            elif f["p"] >= pass_ratio + STRONG_MARGIN:
                status = "得意"
            else:
                status = "ボーダー"
        b = fm[basis] if basis else None
        w, d = fm["written"], fm["drill"]
        result[tag] = {
            "name": s["name"], "weight": s["weight"], "status": status, "basis": basis,
            "p": b["p"] if b else None,
            "sure": bool(b) and status in ("苦手", "得意") and (b["high"] < pass_ratio or b["low"] >= pass_ratio),
            "written": w, "drill": d,
            "main_miss": s["miss"].most_common(1)[0][0] if s["miss"] else None,
            "miss": dict(s["miss"]),
            "weak": [weak_all[k]["label"] for k, _ in s["weak"].most_common(3)],
            "gap": format_gap(w, d, pass_ratio),
        }

    repeated = sorted(
        ({"term": e["label"], "count": e["count"], "tags": [t for t, _ in e["tags"].most_common()]}
         for e in weak_all.values() if e["count"] >= 2),
        key=lambda x: (-x["count"], x["term"]))

    written = [r for r in rows if r["_kind"] == "written"]
    drill = [r for r in rows if r["_kind"] == "drill"]
    return {
        "pass_ratio": pass_ratio,
        "attempts": {"written": len(written), "drill": len(drill)},
        "overall": {
            "written_avg": avg([ratio(r) for r in written]),
            "drill_avg": avg([ratio(r) for r in drill]),
        },
        "miss_type": dict(total_miss),
        "tags": result,
        "repeated_weak": repeated[:10],
    }


def ratio(row):
    return min(max(row["score"] / row["max"], 0.0), 1.0)


def avg(xs):
    return sum(xs) / len(xs) if xs else None


def trend(series):
    """主タグでの結果の前半と後半を比べる。"""
    if len(series) < TREND_MIN:
        return None
    half = len(series) // 2
    delta = avg(series[half:]) - avg(series[:half])
    if delta >= TREND_DELTA:
        return "up"
    if delta <= -TREND_DELTA:
        return "down"
    return "flat"


def format_gap(w, d, pass_ratio):
    """択一と記述式の差から、落とす理由の見当をつける。"""
    if not (w and d and w["enough"] and d["enough"]):
        return None
    if d["p"] >= pass_ratio + 0.1 and w["p"] < pass_ratio:
        return "knows_but_cannot_write"   # 知識はあるが記述で落とす
    if d["p"] < pass_ratio and w["p"] < pass_ratio:
        return "knowledge_first"          # 知識から固める
    if d["p"] < pass_ratio <= w["p"]:
        return "drill_gaps"               # 記述は取れるが択一の細かい知識に穴
    return None


TREND_LABEL = {"up": " ↑", "down": " ↓", "flat": " →", None: ""}
GAP_LABEL = {
    "knows_but_cannot_write": "択一は取れるのに記述で落とす → 知識より読み方・書き方の問題",
    "knowledge_first": "択一も記述も低い → まず知識から固める",
    "drill_gaps": "記述は取れるが択一で落とす → 細かい用語・数値の穴",
}


def pct(x):
    return "—" if x is None else f"{round(x * 100)}%"


def render(a):
    out = []
    P = a["pass_ratio"]
    at = a["attempts"]
    out.append(f"記録: 記述式 {at['written']}件 / 択一 {at['drill']}問　合格ライン {pct(P)}")
    out.append(f"全体: 記述式の平均 {pct(a['overall']['written_avg'])} / 択一の正答率 {pct(a['overall']['drill_avg'])}")
    if at["written"] + at["drill"] == 0:
        out.append("")
        out.append("まだ記録がありません。/question か /drill で解いてから分析してください。")
        return "\n".join(out)

    tags = a["tags"]
    def fmt(label, f, mark):
        if not f:
            return f"{label} —"
        tr = TREND_LABEL[f["trend"]]
        unit = "件" if label == "記述" else "問"
        sub = f"(+副{f['n_sub']})" if f["n_sub"] else ""
        return (f"{mark}{label}{f['n']}{unit}{sub} {pct(f['p'])}"
                f"（{pct(f['low'])}〜{pct(f['high'])}）{'' if f['enough'] else '参考'}{tr}")

    def line(t, s):
        sure = "（確か）" if s["sure"] else ""
        w = fmt("記述", s["written"], "*" if s["basis"] == "written" else "")
        d = fmt("択一", s["drill"], "*" if s["basis"] == "drill" else "")
        return f"  {t:<9}{s['status']}{sure}  {w} / {d}  {s['name']}"

    groups = [
        ("苦手", "苦手（見込みが合格ライン未満）"),
        ("ボーダー", "ボーダー（合格ライン付近）"),
        ("得意", f"得意（合格ライン+{round(STRONG_MARGIN * 100)}pt以上）"),
        ("測定中", "測定中（まだ記録が少ない）"),
    ]
    for status, title in groups:
        items = [(t, s) for t, s in tags.items() if s["status"] == status]
        if not items:
            continue
        # 苦手は「重み × 足りない分」の大きい順、ほかは見込みの低い順
        if status == "苦手":
            items.sort(key=lambda kv: -kv[1]["weight"] * (P - kv[1]["p"]))
        elif status == "測定中":
            items.sort(key=lambda kv: kv[0])
        else:
            items.sort(key=lambda kv: kv[1]["p"])
        out.append("")
        out.append(f"■ {title}")
        for t, s in items:
            out.append(line(t, s))
    untouched = [t for t, s in tags.items() if s["status"] == "未出題"]
    if untouched:
        out.append("")
        out.append("■ 未出題: " + ", ".join(untouched))

    diag = []
    for t, s in tags.items():
        if s["status"] not in ("苦手", "ボーダー"):
            continue
        reasons = []
        if s["gap"]:
            reasons.append(GAP_LABEL[s["gap"]])
        if s["main_miss"]:
            reasons.append(f"ミスは {s['main_miss']} が最多 → {MISS_ADVICE.get(s['main_miss'], '')}")
        if s["weak"]:
            reasons.append("弱点: " + "、".join(s["weak"]))
        if reasons:
            diag.append((t, reasons))
    if diag:
        out.append("")
        out.append("■ 苦手・ボーダーの分野の原因")
        for t, reasons in diag:
            out.append(f"  {t}")
            for r in reasons:
                out.append(f"    - {r}")

    total = sum(a["miss_type"].values())
    if total:
        out.append("")
        out.append("■ ミスの種類: " + ", ".join(
            f"{k} {v}件 ({round(v / total * 100)}%)" for k, v in sorted(a["miss_type"].items(), key=lambda kv: -kv[1])))
    if a["repeated_weak"]:
        out.append("")
        out.append("■ 繰り返している弱点")
        for w in a["repeated_weak"][:5]:
            out.append(f"  {w['term']}  {w['count']}回  ({', '.join(w['tags'])})")
    out.append("")
    out.append("見込み（カッコ内は95%の幅）は新しい結果ほど強く効き、記録が少ないうちは50%に寄せて見積もる。")
    out.append("* は判定に使った形式（記録が足りている形式のうち低い方）。「参考」は記録が少なく判定に使っていない形式。")
    out.append("幅が合格ラインをまたがなければ「確か」。↑↓→ は主タグでの前半と後半の比較。")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    args = ap.parse_args()

    tags = stats.load_tags()
    if not tags:
        print("exam/profile.md に分野タグがありません。/setup-exam で設定してください。", file=sys.stderr)
        return 1
    a = analyze(tags, load_rows(), parse_pass_ratio())
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        print(render(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
