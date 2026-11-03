"""穴埋め（数字・語句で答える問題）の空欄と正解キーの読み取り、採点。

問題文（bank/items/<id>.md）の空欄は全角の角かっこ［ア］で書く。表の中に置いてもよい（仕訳など）。
正解キー（bank/keys/<id>.md）は、本文の「## 解説」より前に、空欄ごとに1行書く：

    - ア：現金
    - イ：50,000
    - ウ：売掛金｜売掛債権      （｜ で区切ると、どれでも正解）

答えと正解は normalize() でそろえてから比べる。数字はカンマ・「円」・全角を無視し、△・▲ はマイナスとして値で比べる。
"""
import re
import unicodedata

BLANK_RE = re.compile(r"［([ア-ンA-Z])］")
KEY_LINE_RE = re.compile(r"^\s*[-*]\s*([ア-ンA-Z])\s*[：:]\s*(.+?)\s*$")
NUM_RE = re.compile(r"^-?(\d+(\.\d*)?|\.\d+)$")
ALT_SPLIT = re.compile(r"[｜|]")
MAX_ANSWER = 200


def blanks(body):
    """問題文に出てくる空欄の記号を、出てきた順に（同じ記号は1回だけ）"""
    out = []
    for m in BLANK_RE.finditer(body or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def key_answers(key_body):
    """正解キーの本文から {記号: [正解, 別解, ...]}。「## 解説」より後は読まない"""
    out = {}
    for line in (key_body or "").splitlines():
        if line.strip().startswith("## 解説"):
            break
        m = KEY_LINE_RE.match(line)
        if m and m.group(1) not in out:
            alts = [a.strip() for a in ALT_SPLIT.split(m.group(2)) if a.strip()]
            if alts:
                out[m.group(1)] = alts
    return out


def normalize(s):
    """比べるための形。全角半角・空白・大文字小文字・数字のカンマと「円」・△▲ をそろえる"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"\s+", "", s).lower()
    num = re.sub(r"[,，]", "", s)
    num = re.sub(r"円$", "", num)
    num = re.sub(r"^[△▲]", "-", num)
    if NUM_RE.match(num):
        # 文字のまま整える（float を通すと、大きな数・長い小数で丸め・あふれが起きる）
        sign = "-" if num.startswith("-") else ""
        whole, _, frac = num.lstrip("-").partition(".")
        whole, frac = whole.lstrip("0") or "0", frac.rstrip("0")
        if whole == "0" and not frac:
            sign = ""
        return sign + whole + ("." + frac if frac else "")
    return s


def grade(blank_labels, key, answers):
    """空欄ごとの採点。answers は {記号: 書いた答え}。返り値は {記号: {"answer", "ok"}}"""
    out = {}
    for b in blank_labels:
        a = str((answers or {}).get(b) or "").strip()
        ok = bool(a) and normalize(a) in {normalize(x) for x in key.get(b, [])}
        out[b] = {"answer": a, "ok": ok}
    return out
