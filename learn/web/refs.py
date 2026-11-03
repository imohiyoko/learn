"""資料（refs/）のアップロード。
"""
import os
import re
import threading
import uuid
from pathlib import Path

from learn.web import files


REF_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".txt", ".md"}

REF_MAX = 50 * 1024 * 1024

REF_DIRS = ("past", "syllabus", "other")

REF_LOCK = threading.Lock()  # 同じ名前の同時アップロードで、先に保存した資料を上書きしないように  # refs/ の下に置くフォルダ（refs/README.md のおすすめに合わせる）


def ref_name(name):
    """アップロードされたファイル名を、refs/<dir>/ の直下に置ける名前にする。だめなら None。"""
    name = (name or "").strip()
    if (not name or len(name) > 120 or name.startswith(".") or "/" in name or "\\" in name
            or re.search(r"[\x00-\x1f\x7f]", name) or Path(name).suffix.lower() not in REF_EXTS):
        return None
    return name


def list_refs():
    if not files.REFS.is_dir():
        return []
    out = []
    for p in files.REFS.rglob("*"):
        rel = p.relative_to(files.REFS)
        if not p.is_file() or rel.as_posix() == "README.md" or any(s.startswith(".") for s in rel.parts):
            continue
        st = p.stat()
        out.append({"path": rel.as_posix(), "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


def save_ref(folder, name, read, size, overwrite=False):
    if folder not in REF_DIRS:
        raise ValueError("置き場所が正しくありません")
    name = ref_name(name)
    if not name:
        raise ValueError("ファイル名が使えません（PDF・画像・テキストだけ。120字以内）")
    if size <= 0:
        raise ValueError("空のファイルです")
    if size > REF_MAX:
        raise ValueError("ファイルが大きすぎます（50MB まで）")
    d = files.REFS / folder
    d.mkdir(parents=True, exist_ok=True)
    dest = d / name
    if dest.exists() and not overwrite:
        raise FileExistsError(f"refs/{folder}/{name} はすでにあります")
    tmp = d / f".{name}.{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "wb") as f:
            left = size
            while left:
                chunk = read(min(left, 1 << 20))
                if not chunk:
                    raise ValueError("アップロードが途中で切れました")
                f.write(chunk)
                left -= len(chunk)
        with REF_LOCK:
            if dest.exists() and not overwrite:
                raise FileExistsError(f"refs/{folder}/{name} はすでにあります")
            os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return f"{folder}/{name}"
