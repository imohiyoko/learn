"""問題バンクの管理画面：問題を1問ずつ一覧・閲覧・編集・削除（ゴミ箱）する。

問題は /prepare が bank/items/<id>.md（本文）と bank/keys/<id>.md（正解キー）に1問1ファイルで作る。
ここでは front matter の tags・concepts（リスト。先頭が主）・difficulty と本文・正解キーを直せる。
本文か正解キーを変えたら verified を false に戻す（verifier の確認を通るまで出さない。次の /prepare が確認する）。
削除は bank/trash/ に移すだけで、戻せる。
"""
import os
import re
import shutil

from learn import core, route
from learn.web import files
from learn.web.files import TAG_RE, read_jsonl, safe_child
from learn.web.sets import busy_items


KINDS = tuple(route.KIND_WEIGHT)  # choice / fill / short / long
MAX_TEXT = 200_000


def trash_dirs():
    t = files.ROOT / "bank" / "trash"
    return t / "items", t / "keys"


def _title(body, n=60):
    """一覧に出す1行：本文の最初の文字のある行（選択肢・見出しの記号は落とす）"""
    for line in body.splitlines():
        s = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if s and not s.startswith("<!--"):
            return s if len(s) <= n else s[:n] + "…"
    return ""


def _difficulty(v):
    try:
        return min(max(int(v), 1), 3)
    except (TypeError, ValueError):
        return None


def _results():
    """log/bank.jsonl の成績を問題ごとに（時刻順）"""
    by = {}
    for r in read_jsonl(files.LOGS["bank"][0]):
        iid = r.get("item")
        if isinstance(iid, str):
            by.setdefault(iid, []).append(r)
    for rows in by.values():
        rows.sort(key=lambda r: str(r.get("graded_at") or ""))
    return by


def _summary(rows):
    if not rows:
        return {"n": 0, "rate": None, "last": None, "last_ok": None}
    got = sum(float(r.get("score") or 0) for r in rows)
    mx = sum(float(r.get("max") or 0) for r in rows)
    last = rows[-1]
    return {"n": len(rows), "rate": got / mx if mx else None, "last": last.get("graded_at"),
            "last_ok": bool(last.get("max")) and float(last.get("score") or 0) >= float(last.get("max") or 0)}


def _row(path, results, busy, trashed=False):
    text = path.read_text(encoding="utf-8")
    meta = core.front_matter(text)
    iid = path.stem
    key_dir = trash_dirs()[1] if trashed else files.BANK_KEYS
    return {"id": iid, "kind": meta.get("kind"), "tags": core.parse_list(meta.get("tags")),
            "concepts": core.parse_list(meta.get("concepts")), "difficulty": _difficulty(meta.get("difficulty")),
            "verified": meta.get("verified") == "true", "has_key": (key_dir / path.name).is_file(),
            "issued_to": meta.get("issued_to") or None, "created_at": meta.get("created_at") or None,
            "title": _title(core.body_of(text)), "mtime": path.stat().st_mtime,
            "stats": _summary(results.get(iid, [])), "busy": iid in busy}


def list_bank():
    """すべての問題（確認待ち・正解キーのないものも含む）と、ゴミ箱の問題"""
    results, busy = _results(), busy_items()
    items, trash = [], []
    if files.BANK_ITEMS.is_dir():
        items = [_row(p, results, busy) for p in sorted(files.BANK_ITEMS.glob("*.md")) if route.ITEM_RE.match(p.stem)]
    tdir = trash_dirs()[0]
    if tdir.is_dir():
        trash = [_row(p, results, set(), True) for p in sorted(tdir.glob("*.md")) if route.ITEM_RE.match(p.stem)]
    concepts = route.load_concepts(route.paths(files.ROOT))
    return {"items": items, "trash": trash, "kinds": list(KINDS),
            "concepts": [{"concept": c, "tag": v["tag"], "name": v["name"]} for c, v in concepts.items()]}


def _item_path(iid, trashed=False):
    base = trash_dirs()[0] if trashed else files.BANK_ITEMS
    p = safe_child(base, iid + ".md")
    if not p or not p.is_file():
        raise LookupError("その問題はありません")
    return p


def bank_detail(iid, with_key=False):
    """問題1問の本文・メタ情報・成績。正解キーは with_key のときだけ（画面で「正解を見る」を押したとき）"""
    trashed = False
    try:
        p = _item_path(iid)
    except LookupError:
        p, trashed = _item_path(iid, True), True
    text = p.read_text(encoding="utf-8")
    row = _row(p, _results(), set() if trashed else busy_items(), trashed)
    out = {**row, "trashed": trashed, "meta": core.front_matter(text), "body": core.body_of(text),
           "history": [{k: r.get(k) for k in ("set", "score", "max", "answer", "confidence", "graded_at", "weak")}
                       for r in _results().get(iid, [])]}
    if with_key:
        kp = (trash_dirs()[1] if trashed else files.BANK_KEYS) / p.name
        out["key"] = kp.read_text(encoding="utf-8") if kp.is_file() else None
    return out


# ---------------------------------------------------------------- 書き換え

FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.S)


def set_meta(text, updates):
    """front matter のキーの値だけを書き換える（行末の # コメントと、ほかのキーはそのまま）。ないキーは末尾に足す"""
    m = FM_RE.match(text)
    if not m:
        raise ValueError("front matter（先頭の --- で囲まれた部分）がありません")
    lines = m.group(1).split("\n")
    done = set()
    for i, line in enumerate(lines):
        km = re.match(r"^([A-Za-z_][\w-]*):(\s*)([^#]*?)(\s+#.*)?$", line)
        if km and km.group(1) in updates and km.group(1) not in done:
            k = km.group(1)
            lines[i] = f"{k}: {updates[k]}{km.group(4) or ''}"
            done.add(k)
    lines += [f"{k}: {v}" for k, v in updates.items() if k not in done]
    return "---\n" + "\n".join(lines) + "\n---\n" + text[m.end():]


def _fmt_list(xs):
    return "[" + ", ".join(xs) + "]"


def _clean_list(v, pattern, what, required):
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s、]+", v) if x]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"{what}はリストで指定してください")
    out = []
    for x in (x.strip() for x in v):
        if not pattern.match(x):
            raise ValueError(f"{what}の形式が正しくありません：{x}")
        if x not in out:
            out.append(x)
    if required and not out:
        raise ValueError(f"{what}を1つ以上指定してください")
    if len(out) > 20:
        raise ValueError(f"{what}が多すぎます")
    return out


def _write(path, text):
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _not_busy(iid):
    if iid in busy_items():
        raise RuntimeError("解いている途中のセットに入っている問題なので、いまは変えられません（セットを提出してから）")


def save_bank(iid, body):
    """tags・concepts・difficulty と、本文（body）・正解キー（key、ファイル全体）を書き換える。
    本文か正解キーが変わったら verified: false に戻す。返り値は保存後の bank_detail"""
    p = _item_path(iid)
    _not_busy(iid)
    text = p.read_text(encoding="utf-8")
    meta = core.front_matter(text)
    updates = {}
    if "tags" in body:
        updates["tags"] = _fmt_list(_clean_list(body["tags"], TAG_RE, "分野タグ", True))
    if "concepts" in body:
        updates["concepts"] = _fmt_list(_clean_list(body["concepts"], route.CONCEPT_RE, "論点",
                                                    meta.get("kind") != "long"))
    if "difficulty" in body:
        d = body["difficulty"]
        if d not in (1, 2, 3, "1", "2", "3"):
            raise ValueError("難しさは 1・2・3 のどれかです")
        updates["difficulty"] = str(d)
    changed = False
    new_body = body.get("body")
    if new_body is not None:
        if not isinstance(new_body, str) or len(new_body) > MAX_TEXT:
            raise ValueError("本文が正しくありません")
        new_body = new_body.replace("\r\n", "\n").strip()
        if not new_body:
            raise ValueError("本文が空です")
        changed |= new_body != core.body_of(text)
    key_text = body.get("key")
    kp = files.BANK_KEYS / p.name
    if key_text is not None:
        if not isinstance(key_text, str) or len(key_text) > MAX_TEXT:
            raise ValueError("正解キーが正しくありません")
        key_text = key_text.replace("\r\n", "\n").rstrip() + "\n"
        if not key_text.strip():
            raise ValueError("正解キーが空です（消すなら問題ごと削除してください）")
        old = kp.read_text(encoding="utf-8") if kp.is_file() else ""
        key_changed = key_text.rstrip() != old.rstrip()
        changed |= key_changed
    else:
        key_changed = False
    if changed:
        updates["verified"] = "false"
    out = set_meta(text, updates) if updates else text
    if new_body is not None:
        m = FM_RE.match(out)
        out = out[:m.end()] + "\n" + new_body + "\n"
    if key_changed:
        kp.parent.mkdir(parents=True, exist_ok=True)
        _write(kp, key_text)
    if out != text:
        _write(p, out)
    return bank_detail(iid)


def trash_bank(iid):
    """問題と正解キーを bank/trash/ に移す（同じ id がゴミ箱にあれば上書き）"""
    p = _item_path(iid)
    _not_busy(iid)
    ti, tk = trash_dirs()
    ti.mkdir(parents=True, exist_ok=True)
    tk.mkdir(parents=True, exist_ok=True)
    kp = files.BANK_KEYS / p.name
    if kp.is_file():
        shutil.move(str(kp), str(tk / p.name))
    else:
        (tk / p.name).unlink(missing_ok=True)
    shutil.move(str(p), str(ti / p.name))


def restore_bank(iid):
    p = _item_path(iid, True)
    if (files.BANK_ITEMS / p.name).exists():
        raise RuntimeError("同じ id の問題がバンクにあります")
    files.BANK_ITEMS.mkdir(parents=True, exist_ok=True)
    kp = trash_dirs()[1] / p.name
    if kp.is_file():
        files.BANK_KEYS.mkdir(parents=True, exist_ok=True)
        if (files.BANK_KEYS / p.name).exists():
            raise RuntimeError("同じ id の正解キーがバンクにあります")
        shutil.move(str(kp), str(files.BANK_KEYS / p.name))
    shutil.move(str(p), str(files.BANK_ITEMS / p.name))
