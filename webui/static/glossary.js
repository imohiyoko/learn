// learn web UI の glossary.js：単語帳とカード。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, api, esc, notice, st } from "./util.js";
import { start } from "./study.js";

// ---------------------------------------------------------- 単語帳
let G = { terms: [], pending: [] };
export async function loadGlossary() {
  G = await api("/api/glossary");
  const tagOpts = st.S ? st.S.tags.map((x) => `<option value="${esc(x.tag)}">${esc(x.name)}</option>`).join("") : "";
  const keep = $("#glossary-tag").value;
  $("#glossary-tag").innerHTML = `<option value="">すべての分野</option>` + tagOpts;
  $("#glossary-tag").value = keep;
  if (!$("#glossary-add-tag").options.length) $("#glossary-add-tag").innerHTML = `<option value="">（なし）</option>` + tagOpts;
  const known = G.terms.filter((x) => x.known).length;
  $("#glossary-summary").textContent = `${G.terms.length}語・覚えた ${known}語`;
  const empty = G.terms.filter((x) => !x.meaning).map((x) => x.term);
  const n = G.pending.length + empty.length;
  $("#glossary-fill").hidden = !n;
  $("#glossary-fill").textContent = `意味がまだ入っていない言葉（${n}件）の意味を Claude に書いてもらう`;
  $("#glossary-fill").dataset.terms = empty.slice(0, 10).join("、");
  renderGlossary();
}

function glossaryView() {
  const q = $("#glossary-q").value.trim().toLowerCase();
  const f = $("#glossary-filter").value, tag = $("#glossary-tag").value;
  return G.terms.filter((x) => (f === "all" || (f === "known") === x.known) && (!tag || x.tags.includes(tag)) &&
    (!q || x.term.toLowerCase().includes(q) || x.meaning.toLowerCase().includes(q)));
}

function renderGlossary() {
  const list = glossaryView();
  const pend = G.pending.length && $("#glossary-filter").value !== "known"
    ? `<p class="muted small">意味がまだ入っていない質問：${G.pending.slice(0, 5).map((p) => esc(p.text)).join("、")}${G.pending.length > 5 ? " ほか" : ""}</p>` : "";
  $("#glossary-list").innerHTML = pend + (list.length ? list.map((x) => `<div class="gl-item${x.known ? " known" : ""}">
      <div><span class="gl-term">${esc(x.term)}</span>
        <span class="muted small">${x.tags.map(esc).join(" ")}${x.asked ? `・${x.asked}回質問` : ""}${x.misses ? `・カードで${x.misses}回まだ` : ""}${x.sources.includes("grade") ? "・採点で不足" : ""}</span></div>
      <label class="small"><input type="checkbox" data-known="${esc(x.term)}"${x.known ? " checked" : ""}> 覚えた</label>
      <div class="gl-meaning md${x.meaning ? "" : " empty"}">${x.meaning ? md.render(x.meaning).html : "（意味はまだありません）"}</div>
    </div>`).join("") : `<p class="muted">${G.terms.length ? "当てはまる言葉はありません" : "まだ言葉がありません。解答中に「言葉の意味がわからない」で聞くと、ここに入ります。"}</p>`);
}
["#glossary-q", "#glossary-filter", "#glossary-tag"].forEach((s) => $(s).addEventListener("input", renderGlossary));
$("#glossary-list").addEventListener("change", async (e) => {
  const c = e.target.closest("[data-known]");
  if (!c) return;
  try {
    await api("/api/glossary/state", { method: "POST", body: { term: c.dataset.known, known: c.checked } });
    await loadGlossary();
  } catch (err) { notice(esc(err.message), "error"); }
});
$("#glossary-fill").addEventListener("click", (e) => start({ action: "glossary", terms: e.target.dataset.terms || "" }));
$("#glossary-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/glossary", { method: "POST", body: { term: f.term.value, meaning: f.meaning.value, tags: f.tag.value ? [f.tag.value] : [] } });
    f.reset();
    await loadGlossary();
  } catch (err) { notice(esc(err.message), "error"); }
});

// カード：まだ覚えていない言葉（意味のあるもの）を、よく聞いた順・間違えた順に出す
let deck = [], deckAt = 0;
function flashShow() {
  const x = deck[deckAt];
  $("#flash-progress").textContent = `${deckAt + 1} / ${deck.length}`;
  $("#flash-term").textContent = x.term;
  $("#flash-tags").textContent = x.tags.join(" ");
  $("#flash-meaning").innerHTML = md.render(x.meaning).html;
  $("#flash-meaning").hidden = true;
  $("#flash-show").hidden = false;
  $("#flash-ok").hidden = $("#flash-again").hidden = true;
}
$("#flash-start").addEventListener("click", () => {
  deck = glossaryView().filter((x) => !x.known && x.meaning);
  if (!deck.length) deck = G.terms.filter((x) => !x.known && x.meaning);
  if (!deck.length) return notice("覚えるカードがありません（意味の入った、まだ覚えていない言葉がない）", "warn");
  deckAt = 0;
  $("#flash").hidden = false;
  flashShow();
  $("#flash").scrollIntoView({ behavior: "smooth" });
});
$("#flash-show").addEventListener("click", () => {
  $("#flash-meaning").hidden = false;
  $("#flash-show").hidden = true;
  $("#flash-ok").hidden = $("#flash-again").hidden = false;
});
async function flashNext(review) {
  const x = deck[deckAt];
  try { await api("/api/glossary/state", { method: "POST", body: { term: x.term, review } }); } catch (e) { notice(esc(e.message), "error"); return; }
  if (review === "again") deck.push(x); // もう一度、最後に出す
  deckAt++;
  if (deckAt >= deck.length) {
    $("#flash").hidden = true;
    notice("カードを一周しました。", "");
    loadGlossary().catch(() => {});
    return;
  }
  flashShow();
}
$("#flash-ok").addEventListener("click", () => flashNext("ok"));
$("#flash-again").addEventListener("click", () => flashNext("again"));
$("#flash-close").addEventListener("click", () => { $("#flash").hidden = true; loadGlossary().catch(() => {}); });
