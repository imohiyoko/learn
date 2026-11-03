"""解答中の質問（log/asks.jsonl）と単語帳（log/glossary.jsonl・log/glossary-state.json）。
"""
import json
import os
import re
import unicodedata

from learn import core
from learn.web import files
from learn.web.files import LOG_LOCK, TAG_RE, append_jsonl, now_iso, read_jsonl, safe_child


ASK_KINDS = {"word": "言葉の意味", "ask": "何を聞かれているか", "worry": "不安・その他"}

PART_RE = re.compile(r"^設問\d+(\(\d+\))?$")


def term_key(s):
    """同じ言葉とみなすための形（全角半角・大文字小文字・空白をそろえる）"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))).lower()


def question_tags(qid):
    p = safe_child(files.VIEWABLE["questions"], qid + ".md")
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
    append_jsonl(files.ASKS, {"id": qid, "tags": tags, "part": part or None, "kind": kind, "text": text, "asked_at": now_iso()})


def load_glossary_state():
    try:
        s = json.loads(files.GLOSSARY_STATE.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def glossary():
    """単語帳を語ごとにまとめる。意味は後の行が優先、分野と出どころは足し合わせる。
    「言葉の意味」の質問で、どの語にも当たらないものは pending として返す（/glossary で意味を作る）"""
    terms = {}
    for row in read_jsonl(files.GLOSSARY):
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
    for a in read_jsonl(files.ASKS):
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
    append_jsonl(files.GLOSSARY, {"term": term, "meaning": meaning, "tags": tags, "source": "manual", "from": None,
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
        files.GLOSSARY_STATE.parent.mkdir(exist_ok=True)
        tmp = files.GLOSSARY_STATE.with_name(files.GLOSSARY_STATE.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, files.GLOSSARY_STATE)
    return s


def known_meaning(key):
    """単語帳にもう意味が入っていれば、その意味（Claude に頼まずに出せる）"""
    return next((t["meaning"] for t in glossary()["terms"] if t["key"] == key and t["meaning"]), None)
