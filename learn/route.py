#!/usr/bin/env python3
"""問題バンク（bank/）から、次に解く問題を機械的に選ぶ。標準ライブラリのみ。

Claude がその場で分野を選ぶと、選び方が毎回ぶれて苦手を測りにくい。ここでは記録から
論点ごとの力を見積もり、決まった規則で問題を選ぶ（同じ記録からは同じセットになる）。

使い方:
  python3 scripts/route.py                   次のセット（択一と短い記述、10問）を表示
  python3 scripts/route.py --n 6 --tag web   分野をしぼる
  python3 scripts/route.py --kinds long      本番形式の長い記述を1問選ぶ（/question が使う）
  python3 scripts/route.py --model           論点ごとの見込みを表示
  python3 scripts/route.py --stock           論点ごとのバンクの在庫と、足りない数を表示（/prepare が使う）
  python3 scripts/route.py --compare <set>   そのセットの前後で、論点ごとの見込みがどう変わったか
  どれも --json で JSON を出す

ファイル:
  exam/concepts.md   論点の一覧（| タグ | 論点ID | 論点 | 重み |）
  bank/items/<id>.md 問題（front matter に kind・tags・concepts・difficulty）
  bank/keys/<id>.md  正解・採点基準・解説（採点が終わるまで学習者に見せない）
  log/bank.jsonl     バンクの問題の成績（1行1問）
  log/history.jsonl  記述式の成績。concepts のある行（バンクの長文）も論点の見込みに使う

選び方:
- 論点ごとに力 θ と不確かさ v を持ち、1問ずつ更新する（analyze.py と同じ Laplace 近似の Elo に、
  問題の難しさ b を足したもの。見込み p = σ(θ - b)）。b は difficulty 1/2/3 → -0.8/0/+0.8
- セットは次の4つの枠から組む。枠の大きさは、まだ測れていない論点の割合で変わる
  1. 洗い出し：測れていない（v が大きい）論点を、分野の重み × 不確かさの順に。難しさ 2 の問題
  2. 穴埋め：見込みが合格ライン未満の論点を、足りない分 × 重みの順に。正答率が 65% 前後になる難しさの問題
  3. やり直し：間違えた問題を、間違えた回数に応じて 1・3・7 日あけてもう一度
  4. 確認：得意な論点のうち、しばらく解いていないもの
- 1つのセットで同じ論点は1問まで、1つの分野からはセットの半分まで
"""
import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from learn import core
from learn.core import DEFAULT_PASS, front_matter, parse_list, parse_pass_ratio, parse_time
from learn.model import V_INIT, elo_update, logit, sigmoid

ROOT = core.ROOT
V_MEASURED = 0.45            # v がこれ以下になったら「測れた」とみなす
DIFF_B = {1: -0.8, 2: 0.0, 3: 0.8}
KIND_WEIGHT = {"choice": 1.0, "short": 1.5, "long": 1.0}
# 択一の確信度（自己申告）で、その1問が見込みを動かす強さを変える。ない行は 1.0（不明）。
# ○でも勘・消去法なら理解の証拠として弱く、根拠があると思って×なら思い込みとして強く効かせる。
CONF_WEIGHT_RIGHT = {"sure": 1.0, "narrowed": 0.6, "guess": 0.3}
CONF_WEIGHT_WRONG = {"sure": 1.3, "narrowed": 1.0, "guess": 0.7}
TARGET_P = 0.65              # 穴埋めで狙う正答率
RETRY_DAYS = (1, 3, 7)       # 間違えた回数ごとの、やり直すまでの日数
REVIEW_AFTER_DAYS = 7        # 得意な論点を確認に出すまでの日数
STRONG_MARGIN = 0.15
STOCK_TARGET = {"choice": 3, "short": 1}   # 論点ごとに用意する数
LONG_PER_TAG = 1                           # 分野ごとに用意する本番形式の長文の数
CONCEPT_RE = re.compile(r"^[a-z][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*$")
ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def paths(root=None):
    root = Path(root) if root else ROOT
    return {
        "root": root,
        "profile": root / "exam" / "profile.md",
        "concepts": root / "exam" / "concepts.md",
        "items": root / "bank" / "items",
        "keys": root / "bank" / "keys",
        "bank_log": root / "log" / "bank.jsonl",
        "history": root / "log" / "history.jsonl",
    }


# ---------------------------------------------------------------- 読み込み

def load_concepts(p):
    """exam/concepts.md の表から {論点ID: {tag, name, weight}} を読む。"""
    out = {}
    if not p["concepts"].is_file():
        return out
    for line in p["concepts"].read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not CONCEPT_RE.match(cells[1]):
            continue
        try:
            w = float(cells[3]) if len(cells) > 3 and cells[3] else 1.0
        except ValueError:
            w = 1.0
        out[cells[1]] = {"tag": cells[0], "name": cells[2], "weight": w}
    return out


def load_items(p, verified_only=True):
    """bank/items/*.md を読む。正解キー（bank/keys）がない問題は使わない。"""
    items = {}
    if not p["items"].is_dir():
        return items
    for f in sorted(p["items"].glob("*.md")):
        iid = f.stem
        if not ITEM_RE.match(iid) or not (p["keys"] / f.name).is_file():
            continue
        meta = front_matter(f.read_text(encoding="utf-8"))
        if verified_only and meta.get("verified") != "true":
            continue
        kind = meta.get("kind")
        if kind not in KIND_WEIGHT:
            continue
        try:
            diff = min(max(int(meta.get("difficulty") or 2), 1), 3)
        except ValueError:
            diff = 2
        try:
            minutes = float(meta.get("time_min") or 0) or None
        except ValueError:
            minutes = None
        items[iid] = {"id": iid, "kind": kind, "tags": parse_list(meta.get("tags")),
                      "concepts": [c for c in parse_list(meta.get("concepts")) if CONCEPT_RE.match(c)],
                      "difficulty": diff, "time_min": minutes, "issued": bool(meta.get("issued_to"))}
    return items


def load_results(p):
    """論点の見込みに使う記録。バンクの成績と、concepts を持つ記述式の成績を時刻順に。"""
    rows = []
    for path, src in ((p["bank_log"], "bank"), (p["history"], "history")):
        for r in core.load_history(path) if path.exists() else []:
            cs = [c for c in (r.get("concepts") or []) if isinstance(c, str) and CONCEPT_RE.match(c)]
            t = parse_time(r.get("graded_at"))
            if not cs or not t:
                continue
            rows.append({**r, "concepts": cs, "_t": t, "_src": src})
    rows.sort(key=lambda r: r["_t"])
    return rows


# ---------------------------------------------------------------- 学習者モデル

def new_model():
    return {"theta": 0.0, "v": V_INIT, "n": 0, "last": None}


def update(m, y, b, w):
    elo_update(m, y, w, b)


def build_model(concepts, results, exclude_set=None):
    models = {c: new_model() for c in concepts}
    for r in results:
        if exclude_set and r.get("set") == exclude_set:
            continue
        y = min(max(r["score"] / r["max"], 0.0), 1.0)
        b = DIFF_B.get(r.get("difficulty"), 0.0)
        w = KIND_WEIGHT.get(r.get("kind"), 1.0) / len(r["concepts"])  # 長文は論点の数で割る
        if r.get("kind") == "choice":
            table = CONF_WEIGHT_RIGHT if y >= 0.5 else CONF_WEIGHT_WRONG
            w *= table.get(r.get("confidence"), 1.0)
        for c in r["concepts"]:
            m = models.setdefault(c, new_model())
            update(m, y, b, w)
            m["n"] += 1
            m["last"] = r["_t"]
    return models


def concept_view(concepts, models, pass_ratio):
    out = {}
    for c, m in models.items():
        p = sigmoid(m["theta"])
        sd = math.sqrt(m["v"])
        if m["n"] == 0:
            status = "未出題"
        elif m["v"] > V_MEASURED:
            status = "測定中"
        elif p < pass_ratio:
            status = "苦手"
        elif p >= pass_ratio + STRONG_MARGIN:
            status = "得意"
        else:
            status = "ボーダー"
        info = concepts.get(c, {"tag": c.split("/")[0], "name": c, "weight": 1.0})
        out[c] = {"concept": c, "tag": info["tag"], "name": info["name"], "weight": info["weight"],
                  "p": round(p, 3), "low": round(sigmoid(m["theta"] - 1.96 * sd), 3),
                  "high": round(sigmoid(m["theta"] + 1.96 * sd), 3), "v": round(m["v"], 3),
                  "n": m["n"], "status": status, "last": m["last"].isoformat() if m["last"] else None}
    return out


# ---------------------------------------------------------------- 出題

def item_history(results):
    """問題ごとの {回数, 間違えた回数, 最後の時刻, 最後に正解したか}"""
    h = {}
    for r in results:
        if r["_src"] != "bank" or not r.get("item"):
            continue
        e = h.setdefault(r["item"], {"n": 0, "miss": 0, "last": None, "ok": False})
        ok = r["score"] >= r["max"]
        e["n"] += 1
        e["miss"] += 0 if ok else 1
        e["last"] = r["_t"]
        e["ok"] = ok
    return h


def tag_weights(p):
    return {t: w for t, (_, w) in core.load_tags(p["profile"]).items()}


def route(root=None, n=10, kinds=("choice", "short"), tag=None, now=None, pass_ratio=DEFAULT_PASS, exclude=()):
    """次のセットを返す：{"items": [{id, kind, concepts, slot, reason}], "shortage": [...], "phase": ...}"""
    p = paths(root)
    now = now or datetime.now(timezone.utc).astimezone()
    concepts = load_concepts(p)
    items = {k: v for k, v in load_items(p).items()
             if v["kind"] in kinds and k not in exclude and not (v["kind"] == "long" and v["issued"])}
    if tag:
        items = {k: v for k, v in items.items() if tag in v["tags"][:1]}
        concepts = {k: v for k, v in concepts.items() if v["tag"] == tag}
    results = load_results(p)
    models = build_model(concepts, results)
    view = concept_view(concepts, models, pass_ratio)
    hist = item_history(results)
    tw = tag_weights(p)

    def cweight(c):
        info = view.get(c) or {}
        return info.get("weight", 1.0) * tw.get(info.get("tag") or c.split("/")[0], 1.0)

    by_concept = defaultdict(list)
    for it in items.values():
        for c in it["concepts"][:1]:  # 主の論点で引く
            by_concept[c].append(it)

    def fresh(it):
        """まだ解いていないか、正解してから日がたった問題"""
        e = hist.get(it["id"])
        return not e or (e["ok"] and now - e["last"] > timedelta(days=30))

    def pick(c, target_b, need_fresh=True):
        cands = [it for it in by_concept.get(c, []) if not need_fresh or fresh(it)]
        if not cands:
            return None
        return min(cands, key=lambda it: (abs(DIFF_B[it["difficulty"]] - target_b),
                                          {"choice": 0, "short": 1, "long": 2}[it["kind"]], it["id"]))

    measured = [c for c in view if view[c]["status"] not in ("未出題", "測定中")]
    ratio_measured = len(measured) / len(view) if view else 1.0
    diagnosing = ratio_measured < 0.7
    phase = "洗い出し" if diagnosing else "穴埋め"
    if diagnosing:
        quota = {"diagnose": 0.6, "remedy": 0.3, "retry": 0.1, "review": 0.0}
    else:
        quota = {"diagnose": 0.1, "remedy": 0.55, "retry": 0.25, "review": 0.1}

    chosen, used_concepts, per_tag = [], set(), Counter()
    tag_cap = max(1, math.ceil(n / 2)) if len({v["tag"] for v in view.values()}) > 1 else n

    def add(it, slot, reason):
        c = it["concepts"][0] if it["concepts"] else None
        t = it["tags"][0] if it["tags"] else None
        if it["id"] in {x["id"] for x in chosen} or (c and c in used_concepts) or (t and per_tag[t] >= tag_cap):
            return False
        chosen.append({"id": it["id"], "kind": it["kind"], "concepts": it["concepts"], "tags": it["tags"],
                       "difficulty": it["difficulty"], "time_min": it["time_min"], "slot": slot, "reason": reason})
        if c:
            used_concepts.add(c)
        if t:
            per_tag[t] += 1
        return True

    def fill(slot, ranked, limit):
        got = 0
        for c, target_b, reason in ranked:
            if len(chosen) >= n or got >= limit:
                break
            it = pick(c, target_b)
            if it and add(it, slot, reason):
                got += 1
        return got

    # 3. やり直し（期日の来た、間違えた問題）
    due = []
    for iid, e in hist.items():
        it = items.get(iid)
        if not it or e["ok"]:
            continue
        wait = RETRY_DAYS[min(e["miss"], len(RETRY_DAYS)) - 1]
        if now - e["last"] >= timedelta(days=wait):
            due.append((e["last"], iid, e["miss"]))
    due.sort()
    retry_limit = max(1, round(n * quota["retry"])) if due else 0
    for _, iid, miss in due:
        if len([x for x in chosen if x["slot"] == "やり直し"]) >= retry_limit:
            break
        add(items[iid], "やり直し", f"前に間違えた問題（{miss}回）")

    # 1. 洗い出し
    diag = sorted((c for c in view if view[c]["status"] in ("未出題", "測定中")),
                  key=lambda c: (-cweight(c) * view[c]["v"], view[c]["n"], c))
    diag_ranked = [(c, 0.0, f"{view[c]['name']}：{'まだ解いていない' if view[c]['n'] == 0 else 'まだ見込みが定まらない'}")
                   for c in diag]
    # 2. 穴埋め：p = σ(θ - b) が TARGET_P になる b = θ - logit(TARGET_P)
    weak = sorted((c for c in view if view[c]["status"] in ("苦手", "ボーダー")),
                  key=lambda c: (-(pass_ratio + STRONG_MARGIN - view[c]["p"]) * cweight(c), c))
    remedy_ranked = [(c, logit(min(max(view[c]["p"], 0.02), 0.98)) - logit(TARGET_P),
                      f"{view[c]['name']}：{view[c]['status']}（見込み {round(view[c]['p'] * 100)}%）") for c in weak]
    # 4. 確認：得意な論点で、しばらく解いていないもの
    stale = sorted((c for c in view if view[c]["status"] == "得意" and view[c]["last"]
                    and now - datetime.fromisoformat(view[c]["last"]) > timedelta(days=REVIEW_AFTER_DAYS)),
                   key=lambda c: view[c]["last"])
    review_ranked = [(c, 0.8, f"{view[c]['name']}：得意（しばらく解いていない）") for c in stale]

    left = n - len(chosen)
    fill("洗い出し", diag_ranked, round(left * quota["diagnose"] / (1 - quota["retry"])) if left else 0)
    fill("穴埋め", remedy_ranked, round(left * quota["remedy"] / (1 - quota["retry"])) if left else 0)
    fill("確認", review_ranked, max(1, round(left * quota["review"])) if quota["review"] else 0)
    # 枠が埋まらなければ、洗い出し → 穴埋め → 確認 → 残りの未出題の順に足す
    for slot, ranked in (("洗い出し", diag_ranked), ("穴埋め", remedy_ranked), ("確認", review_ranked)):
        fill(slot, ranked, n)
    if len(chosen) < n:
        rest = sorted(view, key=lambda c: (view[c]["p"], c))
        fill("穴埋め", [(c, 0.0, f"{view[c]['name']}") for c in rest], n)

    # 在庫が切れて選べなかった論点
    want = [c for c, _, _ in diag_ranked + remedy_ranked]
    shortage = [c for c in want if not any(fresh(it) for it in by_concept.get(c, []))]
    return {"phase": phase, "measured": round(ratio_measured, 2), "items": chosen, "shortage": shortage[:20],
            "pass_ratio": pass_ratio}


def stock(root=None):
    """論点ごとの在庫と、STOCK_TARGET に足りない数。長文は分野ごと。未使用（まだ解いていない）だけ数える。"""
    p = paths(root)
    concepts = load_concepts(p)
    all_items = load_items(p, verified_only=False)
    done = set(item_history(load_results(p)))
    have = defaultdict(Counter)
    for it in all_items.values():
        if it["kind"] == "long" or not it["concepts"]:
            continue
        if it["id"] not in done:
            have[it["concepts"][0]][it["kind"]] += 1
    long_have = Counter(it["tags"][0] for it in all_items.values()
                        if it["kind"] == "long" and it["tags"] and not it["issued"])
    rows = []
    for c, info in concepts.items():
        need = {k: max(0, v - have[c][k]) for k, v in STOCK_TARGET.items()}
        rows.append({"concept": c, "tag": info["tag"], "name": info["name"], "have": dict(have[c]), "need": need})
    tags = sorted({info["tag"] for info in concepts.values()})
    long_rows = [{"tag": t, "have": long_have[t], "need": max(0, LONG_PER_TAG - long_have[t])} for t in tags]
    unverified = sorted(k for k, v in all_items.items() if k not in load_items(p))
    return {"concepts": rows, "long": long_rows, "unverified": unverified,
            "total_need": sum(sum(r["need"].values()) for r in rows) + sum(r["need"] for r in long_rows)}


def compare(set_id, root=None, pass_ratio=DEFAULT_PASS):
    """セットを解く前と後の、論点ごとの見込み"""
    p = paths(root)
    concepts = load_concepts(p)
    results = load_results(p)
    before = concept_view(concepts, build_model(concepts, results, exclude_set=set_id), pass_ratio)
    after = concept_view(concepts, build_model(concepts, results), pass_ratio)
    touched = sorted({c for r in results if r.get("set") == set_id for c in r["concepts"]})
    return [{"concept": c, "name": after[c]["name"], "before": before.get(c, {}).get("p"), "after": after[c]["p"],
             "status_before": before.get(c, {}).get("status"), "status_after": after[c]["status"]}
            for c in touched if c in after]


def model(root=None, pass_ratio=DEFAULT_PASS):
    p = paths(root)
    concepts = load_concepts(p)
    return concept_view(concepts, build_model(concepts, load_results(p)), pass_ratio)


def pass_ratio_of(root=None):
    try:
        return parse_pass_ratio(paths(root)["profile"])
    except Exception:
        return DEFAULT_PASS


# ---------------------------------------------------------------- 表示

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=None, help="問題数（既定：10。--kinds long なら 1）")
    ap.add_argument("--tag")
    ap.add_argument("--kinds", default="choice,short", help="choice,short,long のどれか（カンマ区切り）")
    ap.add_argument("--model", action="store_true")
    ap.add_argument("--stock", action="store_true")
    ap.add_argument("--compare", metavar="SET")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    pr = pass_ratio_of()

    if not load_concepts(paths()):
        print("exam/concepts.md に論点がありません。/prepare で論点の一覧と問題バンクを作ってください。", file=sys.stderr)
        return 1
    if args.stock:
        out = stock()
    elif args.model:
        out = model(pass_ratio=pr)
    elif args.compare:
        out = compare(args.compare, pass_ratio=pr)
    else:
        kinds = tuple(k for k in args.kinds.split(",") if k in KIND_WEIGHT)
        out = route(n=args.n or (1 if kinds == ("long",) else 10), kinds=kinds, tag=args.tag, pass_ratio=pr)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.stock:
        for r in out["concepts"]:
            need = "、".join(f"{k} {v}" for k, v in r["need"].items() if v)
            print(f"{r['concept']:<28} {r['name']}  在庫 {dict(r['have']) or 0}" + (f"  不足 {need}" if need else ""))
        for r in out["long"]:
            print(f"{r['tag'] + '（長文）':<28} 在庫 {r['have']}" + (f"  不足 {r['need']}" if r["need"] else ""))
        if out["unverified"]:
            print(f"verifier の確認待ち: {len(out['unverified'])}問")
        print(f"足りない問題: {out['total_need']}問")
    elif args.model:
        for c, v in sorted(out.items(), key=lambda kv: (kv[1]["tag"], kv[0])):
            print(f"{c:<28} {v['status']:<5} {round(v['p'] * 100):>3}%（{round(v['low'] * 100)}〜{round(v['high'] * 100)}%） {v['n']}問  {v['name']}")
    elif args.compare:
        for r in out:
            b = "—" if r["before"] is None else f"{round(r['before'] * 100)}%"
            print(f"{r['concept']:<28} {b} → {round(r['after'] * 100)}%  {r['status_before']} → {r['status_after']}")
    else:
        print(f"段階: {out['phase']}（測れた論点 {round(out['measured'] * 100)}%）")
        for i, it in enumerate(out["items"], 1):
            print(f"{i:>2}. {it['id']:<32} {it['kind']:<6} [{it['slot']}] {it['reason']}")
        if out["shortage"]:
            print("在庫切れの論点（/prepare で作り足す）: " + ", ".join(out["shortage"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
