// 解答欄を設問ごとに分けるための読み書き（外部ライブラリなし）。
// 問題文の「## 設問」から小設問と字数制限を読み、answers/<id>.md（templates/answer.md の形）と行き来する。
(function () {
  const SUB_RE = /^\s*[（(]\s*(\d+)\s*[)）]\s*(.*)$/; // (1) / （1）
  const BIG_RE = /^\s*\*{0,2}\s*設問\s*(\d+)\s*\*{0,2}\s*(.*)$/; // **設問1** ...
  const LIMIT_RE = /(\d+)\s*字以内/;

  // 問題文から解答欄の一覧を作る。[{key: "1-2", label: "設問1(2)", big, sub, limit, prompt}]
  function fields(qtext) {
    const body = qtext.split(/\r?\n/);
    const start = body.findIndex((l) => /^##\s*設問\s*$/.test(l));
    if (start < 0) return [];
    const out = [];
    let big = null;
    let bigItem = null; // 小設問のない大設問は、それ自体を1つの欄にする
    for (const line of body.slice(start + 1)) {
      if (/^##\s/.test(line)) break;
      const b = line.match(BIG_RE);
      if (b) {
        big = +b[1];
        bigItem = { key: `${big}`, label: `設問${big}`, big, sub: null, limit: limit(b[2]), prompt: b[2].trim() };
        out.push(bigItem);
        continue;
      }
      const s = big != null && line.match(SUB_RE);
      if (s) {
        if (bigItem) { out.splice(out.indexOf(bigItem), 1); bigItem = null; }
        out.push({ key: `${big}-${+s[1]}`, label: `設問${big}(${+s[1]})`, big, sub: +s[1], limit: limit(s[2]), prompt: s[2].trim() });
      }
    }
    return out;
  }

  function limit(s) {
    const m = (s || "").match(LIMIT_RE);
    return m ? +m[1] : null;
  }

  // 字数：改行は数えない。それ以外（空白・英数字も）は1字
  function count(s) {
    return Array.from(s.replace(/\r?\n/g, "")).length;
  }

  // 解答ファイルを欄ごとの値に分ける。欄に収まらない書き込みがあれば null（まとめて編集に切り替える）
  function parse(text, fs) {
    const lines = text.split(/\r?\n/);
    const first = lines.findIndex((l) => /^##\s*設問/.test(l));
    const header = (first < 0 ? lines : lines.slice(0, first)).join("\n").replace(/\s+$/, "");
    const values = {};
    const keys = new Set(fs.map((f) => f.key));
    // 見出しより前に書いた本文（front matter と「# 解答」以外）も、欄には収まらない
    if (header.replace(/^---\n[\s\S]*?\n---\n?/, "").replace(/^#\s*解答\s*$/m, "").trim()) return null;
    if (first < 0) return { header, values };
    let big = null;
    let key = null;
    let stray = false;
    const put = (l) => {
      if (key && keys.has(key)) values[key] = values[key] == null ? l : values[key] + "\n" + l;
      else if (l.trim()) stray = true;
    };
    for (const line of lines.slice(first)) {
      const h = line.match(/^##\s*設問\s*(\d+)\s*$/);
      if (h) { big = +h[1]; key = `${big}`; continue; }
      if (/^#/.test(line)) { key = null; put(line); continue; }
      // 小設問の見出しは「(n)」だけの行（build が書く形）。本文中の「(2)は〜」のような行は本文として扱う
      const s = big != null && line.match(SUB_RE);
      if (s && !s[2].trim()) {
        const k = `${big}-${+s[1]}`;
        if (!keys.has(k)) return null; // 問題文にない小設問は欄に分けられない
        key = k;
        continue;
      }
      put(line);
    }
    if (stray) return null;
    for (const k in values) values[k] = values[k].replace(/^\s*\n/, "").replace(/\s+$/, "");
    return { header, values };
  }

  function header(id, startedAt) {
    return `---\nid: ${id}\nstarted_at: ${startedAt}\nsubmitted_at:\n---\n\n# 解答`;
  }

  // 欄ごとの値を解答ファイルに戻す（templates/answer.md と同じ形）
  function build(head, fs, values) {
    let out = head.replace(/\s+$/, "") + "\n";
    let big = null;
    for (const f of fs) {
      if (f.big !== big) { big = f.big; out += `\n## 設問${big}\n`; }
      const v = (values[f.key] || "").replace(/\s+$/, "");
      out += f.sub == null ? `\n${v}\n` : `\n(${f.sub})\n${v}\n`;
    }
    return out;
  }

  // front matter の1項目を書き換える（なければ足す）。提出時刻やタイマーの時間を残すのに使う
  function setMeta(text, key, value) {
    const m = text.match(/^---\n([\s\S]*?)\n---/);
    if (!m) return `---\n${key}: ${value}\n---\n\n` + text;
    const lines = m[1].split("\n");
    const i = lines.findIndex((l) => l.startsWith(key + ":"));
    if (i >= 0) lines[i] = `${key}: ${value}`;
    else lines.push(`${key}: ${value}`);
    return `---\n${lines.join("\n")}\n---` + text.slice(m[0].length);
  }

  // front matter の1項目を消す（なければそのまま）
  function delMeta(text, key) {
    const m = text.match(/^---\n([\s\S]*?)\n---/);
    if (!m) return text;
    const lines = m[1].split("\n").filter((l) => !l.startsWith(key + ":"));
    return `---\n${lines.join("\n")}\n---` + text.slice(m[0].length);
  }

  const api = { fields, count, parse, build, header, setMeta, delMeta };
  if (typeof window !== "undefined") window.answer = api;
  if (typeof module !== "undefined") module.exports = api;
})();
