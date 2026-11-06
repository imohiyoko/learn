"""問題バンクとセット。択一は答えたその場で正解キーと照らして log/bank.jsonl に書く。
"""
import json
import os
import re
import threading
from collections import Counter
from datetime import datetime

from learn import core, fill, route
from learn.web import files
from learn.web.files import TAG_RE, append_jsonl, now_iso, read_jsonl, safe_child
from learn.web.glossary import known_meaning, term_key


SET_RE = re.compile(r"^set-\d{8}-\d{6}(-\d+)?$")

CHOICE_RE = re.compile(r"^\s*[-*]\s*([ア-ンA-Z])[\s　.．:：)）]\s*(.+)$")

SET_LOCK = threading.RLock()


def _bank_file(base, sub, iid):
    """バンクのファイル。ゴミ箱（bank/trash/）に移した問題も、済んだセットの見直しのために読む"""
    for d in (base, files.ROOT / "bank" / "trash" / sub):
        p = safe_child(d, iid + ".md")
        if p and p.is_file():
            return p
    return None


def bank_item(iid):
    """問題の本文と選択肢。正解キーは含めない"""
    p = _bank_file(files.BANK_ITEMS, "items", iid)
    if not p:
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
    body = "\n".join(lines).strip()
    return {"id": iid, "kind": meta.get("kind"), "tags": core.parse_list(meta.get("tags")),
            "concepts": core.parse_list(meta.get("concepts")), "difficulty": meta.get("difficulty"),
            "time_min": meta.get("time_min"), "verified": meta.get("verified") == "true",
            "body": body, "options": options, "blanks": fill.blanks(body) if meta.get("kind") == "fill" else []}


def bank_key(iid):
    p = _bank_file(files.BANK_KEYS, "keys", iid)
    if not p:
        return None
    text = p.read_text(encoding="utf-8")
    return {"meta": core.front_matter(text), "body": core.body_of(text)}


def set_dir(sid):
    return safe_child(files.SETS, sid) if SET_RE.match(sid or "") else None


def load_set(sid):
    d = set_dir(sid)
    try:
        return json.loads((d / "set.json").read_text(encoding="utf-8")) if d else None
    except (OSError, ValueError):
        return None


def save_set(s):
    d = files.SETS / s["id"]
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "set.json.tmp"
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "set.json")


def concept_names():
    return {c: v["name"] for c, v in route.load_concepts(route.paths(files.ROOT)).items()}


def bank_status():
    p = route.paths(files.ROOT)
    if not route.load_concepts(p):
        return {"ready": False, "concepts": 0, "items": 0}
    st = route.stock(files.ROOT)
    items = route.load_items(p)
    pr = core.parse_pass_ratio(files.PROFILE)
    return {"ready": bool(items), "concepts": len(st["concepts"]), "items": len(items),
            "kinds": dict(Counter(v["kind"] for v in items.values())), "need": st["total_need"],
            "unverified": len(st["unverified"]), "model": route.model(files.ROOT, pass_ratio=pr), "stock": st}


SET_KINDS = {"mixed": ("choice", "fill", "short"), "choice": ("choice",)}  # 「今日のセット」と、択一だけのドリル
AUTO_KINDS = ("choice", "fill")  # 答えたその場で正解キーと照らして採点する形式


def create_set(n=10, tag=None, kind="mixed"):
    if tag and not TAG_RE.match(tag):
        raise ValueError("タグの形式が正しくありません")
    if kind not in SET_KINDS:
        raise ValueError("セットの種類が正しくありません")
    if not 1 <= n <= 30:
        raise ValueError("問題数は1〜30です")
    with SET_LOCK:  # 同時に作っても、同じ問題が2つのセットに入らないように
        # 穴埋めは、profile に「穴埋め問題」の節があるときだけ出す（節を消せば、バンクに残っていても出さない）
        kinds = tuple(k for k in SET_KINDS[kind] if k != "fill" or core.has_section(files.PROFILE, "穴埋め問題"))
        r = route.route(files.ROOT, n=n, kinds=kinds, tag=tag or None,
                        pass_ratio=core.parse_pass_ratio(files.PROFILE), exclude=busy_items())
        if not r["items"]:
            raise RuntimeError("出せる問題がバンクにありません。「問題バンクを作る」で用意してください")
        base = "set-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        sid, k = base, 1
        while (files.SETS / sid).exists():
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
    if not files.SETS.is_dir():
        return []
    out = []
    for d in files.SETS.iterdir():
        s = load_set(d.name) if SET_RE.match(d.name) else None
        if not s:
            continue
        res = s.get("results", {})
        out.append({"id": s["id"], "created": s.get("created"), "status": set_status(s), "phase": s.get("phase"),
                    "n": len(s["items"]), "answered": len(s.get("answers", {})),
                    "correct": sum(1 for r in res.values() if r.get("correct")),
                    "choices": sum(1 for it in s["items"] if it["kind"] in AUTO_KINDS),
                    "report": (files.REPORTS / f"{s['id']}.md").is_file()})
    return sorted(out, key=lambda x: x["id"], reverse=True)


def set_status(s):
    if (files.REPORTS / f"{s['id']}.md").is_file():
        return "graded"
    return s.get("status", "open")


def set_view(s):
    """画面に出すセット。正解と解説は、答えた択一・穴埋めと、採点の終わった記述にだけ付ける"""
    status = set_status(s)
    names = concept_names()
    results = dict(s.get("results", {}))
    if status == "graded":  # 記述の点数は /grade-set が log/bank.jsonl に書いたもの
        for r in read_jsonl(files.LOGS["bank"][0]):
            if r.get("set") == s["id"] and r.get("kind") == "short" and core.is_num(r.get("score")) and core.is_num(r.get("max")) and r["max"] > 0:
                results[r.get("item")] = {"score": r["score"], "max": r["max"], "correct": r["score"] >= r["max"] * 0.6}
    items = []
    for it in s["items"]:
        b = bank_item(it["id"]) or {"id": it["id"], "body": "（問題が見つかりません）", "options": []}
        v = {**it, "body": b["body"], "options": b["options"], "blanks": b.get("blanks", []), "concept_names": [names.get(c, c) for c in it["concepts"]],
             "answer": s.get("answers", {}).get(it["id"]), "result": results.get(it["id"])}
        # 正解が複数ありうるとして確認待ちに戻した問題（verified: false）は、正解を出さない
        if ((it["kind"] in AUTO_KINDS and v["result"]) or status == "graded") and b.get("verified", True):
            k = bank_key(it["id"])
            if k:
                v["key"] = {"answer": k["meta"].get("answer"), "body": k["body"]}
        items.append(v)
    out = {k: s.get(k) for k in ("id", "created", "tag", "kind", "want", "phase", "shortage")}
    out.update({"status": status, "items": items})
    rp = files.REPORTS / f"{s['id']}.md"
    if rp.is_file():
        out["report"] = rp.read_text(encoding="utf-8")
        out["compare"] = route.compare(s["id"], files.ROOT, pass_ratio=core.parse_pass_ratio(files.PROFILE))
    return out


def confidence_of(value, unsure):
    """選択肢ごとの「不安」の印から確信度を決める。選んだ答えに印があれば勘、ほかの選択肢だけなら消去法。
    印がなければ None（付け忘れもあるので「根拠がわかる」とは決めつけない）"""
    if not unsure:
        return None
    return "guess" if value in unsure else "narrowed"


def answer_set_item(sid, iid, value, unsure=None):
    with SET_LOCK:
        s = load_set(sid)
        if not s:
            raise LookupError("そのセットはありません")
        if set_status(s) != "open":
            raise RuntimeError("提出済みのセットです")
        it = next((x for x in s["items"] if x["id"] == iid), None)
        if not it:
            raise LookupError("その問題はこのセットにありません")
        if it["kind"] == "fill":
            answer_fill(s, sid, iid, it, value)
            save_set(s)
            return s
        value = str(value or "").strip()
        if it["kind"] == "choice":
            if iid in s["results"]:
                raise RuntimeError("この問題はもう答えています")
            b, k = bank_item(iid), bank_key(iid)
            if not k or not b:
                raise LookupError("正解キーがありません")
            if value not in {o["label"] for o in b["options"]}:
                raise ValueError("選択肢から選んでください")
            labels = {o["label"] for o in b["options"]}
            if unsure is None:
                unsure = []
            if not isinstance(unsure, list) or not all(isinstance(x, str) and x in labels for x in unsure):
                raise ValueError("不安の印は選択肢の記号で指定してください")
            unsure = sorted(set(unsure))
            confidence = confidence_of(value, unsure)
            correct = value == k["meta"].get("answer", "").strip()
            names = concept_names()
            line = {
                "id": f"{sid}-{iid}", "set": sid, "item": iid, "kind": "choice", "tags": it["tags"] or ["unknown"],
                "concepts": it["concepts"], "difficulty": it["difficulty"], "score": 1 if correct else 0, "max": 1,
                "answer": value, "graded_at": now_iso(),
                "weak": [] if correct else [names.get(c, c) for c in it["concepts"][:1]], "miss_type": {}}
            if unsure:
                line["unsure"] = unsure
                line["confidence"] = confidence
            append_jsonl(files.LOGS["bank"][0], line)
            s["answers"][iid] = value
            s["results"][iid] = {"correct": correct, "graded_at": now_iso()}
            if unsure:
                s["results"][iid].update({"unsure": unsure, "confidence": confidence})
        else:
            if len(value) > 4000:
                raise ValueError("解答が長すぎます")
            s["answers"][iid] = value
            d = files.SETS / sid / "answers"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{iid}.md").write_text(value + "\n", encoding="utf-8")
        save_set(s)
        return s


def answer_fill(s, sid, iid, it, value):
    """穴埋めを、正解キーの空欄ごとの正解（と別解）と照らして採点する。value は {記号: 答え}"""
    if iid in s["results"]:
        raise RuntimeError("この問題はもう答えています")
    b, k = bank_item(iid), bank_key(iid)
    key = fill.key_answers(k["body"]) if k else {}
    if not b or not b["blanks"] or any(x not in key for x in b["blanks"]):
        raise LookupError("正解キーがありません")
    if not isinstance(value, dict) or not all(isinstance(v, str) and len(v) <= fill.MAX_ANSWER for v in value.values()):
        raise ValueError(f"空欄ごとに{fill.MAX_ANSWER}字以内で答えてください")
    if not any(str(value.get(x) or "").strip() for x in b["blanks"]):
        raise ValueError("空欄に答えを書いてください")
    graded = fill.grade(b["blanks"], key, value)
    got = sum(1 for g in graded.values() if g["ok"])
    correct = got == len(graded)
    names = concept_names()
    append_jsonl(files.LOGS["bank"][0], {
        "id": f"{sid}-{iid}", "set": sid, "item": iid, "kind": "fill", "tags": it["tags"] or ["unknown"],
        "concepts": it["concepts"], "difficulty": it["difficulty"], "score": got, "max": len(graded),
        "answer": " / ".join(f"{x}={g['answer']}" for x, g in graded.items()), "blanks": graded, "graded_at": now_iso(),
        "weak": [] if correct else [names.get(c, c) for c in it["concepts"][:1]], "miss_type": {}})
    s["answers"][iid] = {x: g["answer"] for x, g in graded.items()}
    s["results"][iid] = {"correct": correct, "score": got, "max": len(graded), "blanks": graded, "graded_at": now_iso()}


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
    append_jsonl(files.ASKS, {"id": iid, "set": sid, "tags": it.get("tags") or [], "concepts": it.get("concepts") or [],
                              "part": None, "kind": "word", "text": term, "asked_at": now_iso()})
    k = term_key(term)
    if it["kind"] == "choice" and not answered:
        b = bank_item(iid) or {"options": []}
        if any(k in term_key(o["text"]) for o in b["options"]):
            return {"deferred": True}
    return {"deferred": False, "meaning": known_meaning(k)}


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
