"""ホーム画面の状態（試験の設定、おすすめ、出題済みの問題、分析）。
"""
import re
import shutil

from learn import analyze, core, route
from learn.web import files
from learn.web.jobs import claude_bin
from learn.web.sets import busy_items


def profile_status():
    if not files.PROFILE.exists():
        return "missing"
    text = files.PROFILE.read_text(encoding="utf-8")
    if "<!-- TODO" in text or not core.load_tags(files.PROFILE):
        return "todo"
    return "ok"


def exam_name():
    if not files.PROFILE.exists():
        return None
    m = re.search(r"^- 名称：(.+)$", files.PROFILE.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else None


def drill_default_count():
    if files.PROFILE.exists():
        m = re.search(r"^- 1回の問題数：\s*(\d+)", files.PROFILE.read_text(encoding="utf-8"), re.M)
        if m:
            return int(m.group(1))
    return 10


def recommend(kinds, n=3):
    """問題バンクから route.py が次に選ぶ問題の分野を [{tag, name, reason}] で返す（出題と同じ選び方）。
    バンクに問題がなければ空。"""
    tags = core.load_tags(files.PROFILE)
    if not tags:
        return []
    try:
        # 長文は次の1問だけを出すので、おすすめも1分野。解きかけのセットの問題は、セットを作るときと同じく除く
        r = route.route(files.ROOT, n=1 if kinds == ("long",) else 10, kinds=kinds,
                        pass_ratio=core.parse_pass_ratio(files.PROFILE), exclude=busy_items())
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
    if not files.EXAMPLES.is_dir():
        return []
    out = []
    for d in sorted(files.EXAMPLES.iterdir()):
        p = d / "profile.md"
        if p.is_file():
            m = re.search(r"^- 名称：(.+)$", p.read_text(encoding="utf-8"), re.M)
            out.append({"key": d.name, "name": m.group(1).strip() if m else d.name})
    return out


def list_items():
    """出題済みの問題を新しい順に。採点済みかどうかと点数も付ける。"""
    qdir = files.VIEWABLE["questions"]
    if not qdir.is_dir():
        return []
    scores = {}
    for name in ("history", "drill"):
        path, _ = files.LOGS[name]
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
            "graded": (files.VIEWABLE["results"] / f"{qid}.md").exists(),
            "has_answer": (files.VIEWABLE["answers"] / f"{qid}.md").exists(),
            "score": scores.get(qid),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def state(jobs, runner):
    tags = core.load_tags(files.PROFILE)
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
    tags = core.load_tags(files.PROFILE)
    if not tags:
        return None
    a = analyze.analyze(tags, analyze.load_rows(files.LOGS["history"][0], files.LOGS["drill"][0], files.LOGS["bank"][0]), core.parse_pass_ratio(files.PROFILE),
                        asks=analyze.load_asks(files.ASKS))
    # 画面用に、analyze.py が文字の表示に使うラベルを添える
    for t in a["tags"].values():
        t["gap_label"] = analyze.GAP_LABEL.get(t.get("gap"))
        for kind in ("written", "drill"):
            if t.get(kind):
                t[kind]["trend_label"] = analyze.TREND_LABEL.get(t[kind].get("trend"), "").strip()
    return a
