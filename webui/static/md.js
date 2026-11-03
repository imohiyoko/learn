// 問題・講評を表示するための小さな Markdown 変換（外部ライブラリなし）。
// 見出し・段落・リスト・表・引用・コードブロック・強調・リンク・画像だけを扱う。
(function () {
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function safeUrl(u) {
    u = u.trim();
    if (/^(https?:|\/|#)/i.test(u)) return u;
    if (/^[a-z][a-z0-9+.-]*:/i.test(u)) return "#"; // javascript: などは使わせない
    // 問題からの相対パス assets/xxx は、サーバーの /assets/xxx で配る
    return u.replace(/^(\.\/)?(questions\/)?assets\//, "/assets/");
  }

  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = esc(s);
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, u) => `<img alt="${alt}" src="${esc(safeUrl(u))}">`);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) => `<a href="${esc(safeUrl(u))}" target="_blank" rel="noopener">${t}</a>`);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    s = s.replace(/&lt;!--[\s\S]*?--&gt;/g, (m) => `<span class="comment">${m}</span>`);
    s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
    return s;
  }

  function splitRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  }

  function render(src) {
    const meta = {};
    src = src.replace(/\r\n/g, "\n");
    const fm = src.match(/^---\n([\s\S]*?)\n---\n/);
    if (fm) {
      fm[1].split("\n").forEach((l) => {
        const i = l.indexOf(":");
        if (i > 0) meta[l.slice(0, i).trim()] = l.slice(i + 1).trim();
      });
      src = src.slice(fm[0].length);
    }
    const lines = src.split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if ((m = line.match(/^```(\w*)/))) {
        const buf = [];
        i++;
        while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
        i++;
        out.push(`<pre class="lang-${esc(m[1] || "text")}"><code>${esc(buf.join("\n"))}</code></pre>`);
        continue;
      }
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        const n = Math.min(m[1].length + 1, 6);
        out.push(`<h${n}>${inline(m[2])}</h${n}>`);
        i++;
        continue;
      }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (line.trim().startsWith("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(splitRow(lines[i++]));
        out.push("<div class=\"table\"><table><thead><tr>" + head.map((c) => `<th>${inline(c)}</th>`).join("") +
          "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
          "</tbody></table></div>");
        continue;
      }
      if (/^\s*>/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
        out.push(`<blockquote>${render(buf.join("\n")).html}</blockquote>`);
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        // 字下げでネストするリスト
        const items = [];
        while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
          const lm = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (lm) items.push({ indent: lm[1].length, ordered: /\d/.test(lm[2]), text: lm[3] });
          else items[items.length - 1].text += " " + lines[i].trim();
          i++;
        }
        out.push(renderList(items, 0, items.length));
        continue;
      }
      if (!line.trim()) { i++; continue; }
      const buf = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*\||\s*>|\s*([-*+]|\d+[.)])\s)/.test(lines[i])) buf.push(lines[i++]);
      if (!buf.length) buf.push(lines[i++]);
      out.push(`<p>${buf.map(inline).join("<br>")}</p>`);
    }
    return { meta, html: out.join("\n") };
  }

  function renderList(items, from, to) {
    if (from >= to) return "";
    const base = items[from].indent;
    const tag = items[from].ordered ? "ol" : "ul";
    let html = `<${tag}>`;
    let k = from;
    while (k < to) {
      let j = k + 1;
      while (j < to && items[j].indent > base) j++;
      const t = items[k].text.replace(/^\[( |x)\]\s*/, (_, c) => (c === "x" ? "☑ " : "☐ "));
      html += `<li>${inline(t)}${renderList(items, k + 1, j)}</li>`;
      k = j;
    }
    return html + `</${tag}>`;
  }

  window.md = { render, esc };
})();
