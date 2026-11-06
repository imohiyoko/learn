// learn web UI の bank.js：問題（バンクの問題を1問ずつ一覧・閲覧・編集・削除）と論点のページ。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, api, esc, notice, st } from "./util.js";
import { go } from "./main.js";
import { pct } from "./stats.js";
import { STATUS_ORDER } from "./home.js";

export const KIND_LABEL = { choice: "択一", fill: "穴埋め", short: "短い記述", long: "長文" };
const DIFF_LABEL = { 1: "易", 2: "普", 3: "難" };
const FILTERS = ["q", "kind", "tag", "concept", "diff", "status", "sort"];

let B = null; // /api/bank/items の結果
let bankQuery = ""; // 一覧の絞り込み（問題のページから戻るときに使う）

const opt = (v, label, cur) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
const tagName = (t) => ((st.S && st.S.tags) || []).find((x) => x.tag === t)?.name || t;

function stateBadge(x) {
  if (!x.has_key) return `<span class="badge ng">正解キーなし</span>`;
  if (!x.verified) return `<span class="badge warn">確認待ち</span>`;
  return `<span class="badge ok">確認済み</span>`;
}

function statsCell(s) {
  if (!s.n) return `<span class="muted">まだ</span>`;
  return `${pct(s.rate)} <span class="sub">${s.n}回${s.last_ok === false ? "・前回×" : ""}</span>`;
}

// ---------------------------------------------------------- 一覧
export async function loadBank(q) {
  B = await api("/api/bank/items");
  if (location.pathname !== "/bank") return; // 読み込むあいだに別のページへ移った
  const f = Object.fromEntries(FILTERS.map((k) => [k, q.get(k) || ""]));
  $("#bank-q").value = f.q;
  $("#bank-kind").innerHTML = opt("", "形式：すべて", f.kind) + B.kinds.map((k) => opt(k, KIND_LABEL[k] || k, f.kind)).join("");
  const tags = [...new Set([...((st.S && st.S.tags) || []).map((t) => t.tag), ...B.items.flatMap((x) => x.tags)])];
  $("#bank-tag").innerHTML = opt("", "分野：すべて", f.tag) + tags.map((t) => opt(t, `${t}：${tagName(t)}`, f.tag)).join("");
  $("#bank-concept").innerHTML = opt("", "論点：すべて", f.concept) +
    B.concepts.map((c) => opt(c.concept, `${c.name}（${c.concept}）`, f.concept)).join("");
  $("#bank-diff").value = f.diff;
  $("#bank-status").value = f.status;
  $("#bank-sort").value = f.sort || "id";
  drawBank();
  drawTrash();
}

function currentFilter() {
  return { q: $("#bank-q").value.trim(), kind: $("#bank-kind").value, tag: $("#bank-tag").value, concept: $("#bank-concept").value,
    diff: $("#bank-diff").value, status: $("#bank-status").value, sort: $("#bank-sort").value === "id" ? "" : $("#bank-sort").value };
}

function drawBank() {
  if (!B) return;
  const f = currentFilter();
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  bankQuery = qs ? "?" + qs : "";
  if (location.pathname === "/bank" && location.search !== bankQuery) history.replaceState(null, "", "/bank" + bankQuery);
  const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
  let rows = B.items.filter((x) => (!f.kind || x.kind === f.kind) && (!f.tag || x.tags.includes(f.tag))
    && (!f.concept || x.concepts.includes(f.concept)) && (!f.diff || String(x.difficulty) === f.diff)
    && (!f.status || (f.status === "verified" ? x.verified && x.has_key : f.status === "unverified" ? !x.verified
      : f.status === "nokey" ? !x.has_key : f.status === "new" ? !x.stats.n : x.stats.last_ok === false))
    && words.every((w) => (x.id + " " + x.title).toLowerCase().includes(w)));
  const by = { new: (a, b) => b.mtime - a.mtime, rate: (a, b) => (a.stats.rate ?? 2) - (b.stats.rate ?? 2) || b.stats.n - a.stats.n,
    n: (a, b) => b.stats.n - a.stats.n };
  if (by[f.sort]) rows = rows.slice().sort(by[f.sort]);
  const unv = B.items.filter((x) => !x.verified).length;
  $("#bank-summary").textContent = `${rows.length} / ${B.items.length}問` + (unv ? `（確認待ち ${unv}問）` : "");
  const cname = Object.fromEntries(B.concepts.map((c) => [c.concept, c.name]));
  $("#bank-list").innerHTML = rows.length ? rows.map((x) => `<tr data-id="${esc(x.id)}">
      <td><a href="/bank/${encodeURIComponent(x.id)}">${esc(x.id)}</a><div class="sub">${esc(x.title)}</div></td>
      <td>${esc(KIND_LABEL[x.kind] || x.kind || "?")}</td>
      <td>${x.tags.map((t, i) => (i ? `<span class="sub">${esc(t)}</span>` : `<b>${esc(t)}</b>`)).join(" ")}
        <div class="sub">${x.concepts.map((c) => esc(cname[c] || c)).join("、")}</div></td>
      <td>${x.difficulty ? esc(DIFF_LABEL[x.difficulty]) : "—"}</td>
      <td>${stateBadge(x)}${x.busy ? `<div class="sub">解いている途中のセット</div>` : ""}${x.issued_to ? `<div class="sub">出題済み</div>` : ""}</td>
      <td>${statsCell(x.stats)}</td></tr>`).join("")
    : `<tr><td colspan="6" class="muted">${B.items.length ? "条件に合う問題がありません" : "まだ問題がありません。ホームの「問題バンクを作る」（/prepare）で作れます"}</td></tr>`;
}

function drawTrash() {
  $("#bank-trash").hidden = !B.trash.length;
  $("#bank-trash-n").textContent = B.trash.length ? `${B.trash.length}問` : "";
  $("#bank-trash-list").innerHTML = B.trash.map((x) => `<tr>
    <td><a href="/bank/${encodeURIComponent(x.id)}">${esc(x.id)}</a><div class="sub">${esc(x.title)}</div></td>
    <td>${esc(KIND_LABEL[x.kind] || x.kind || "?")}</td><td>${esc(x.tags.join(", "))}</td>
    <td><button class="small" data-restore="${esc(x.id)}" type="button">戻す</button></td></tr>`).join("");
}

for (const id of ["#bank-q", "#bank-kind", "#bank-tag", "#bank-concept", "#bank-diff", "#bank-status", "#bank-sort"]) {
  $(id).addEventListener(id === "#bank-q" ? "input" : "change", drawBank);
}
$("#bank-list").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr || e.target.closest("a")) return;
  go("/bank/" + encodeURIComponent(tr.dataset.id));
});
$("#bank-trash-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-restore]");
  if (!b) return;
  try {
    await api(`/api/bank/items/${encodeURIComponent(b.dataset.restore)}/restore`, { method: "POST", body: {} });
    notice(`${esc(b.dataset.restore)} をバンクに戻しました。`);
    await loadBank(new URLSearchParams(location.search));
  } catch (err) { notice(esc(err.message), "error"); }
});

// ---------------------------------------------------------- 1問
let I = null; // いま開いている問題
let itemGen = 0;

export async function loadBankItem(id) {
  const gen = ++itemGen;
  I = null;
  $("#bi-back").setAttribute("href", "/bank" + bankQuery);
  $("#bi-title").textContent = id;
  $("#bi-body").innerHTML = `<p class="muted">読み込み中…</p>`;
  $("#bi-key").hidden = true;
  $("#bi-key").innerHTML = "";
  $("#bi-key-show").hidden = false;
  $("#bi-key-help").hidden = false;
  closeEdit();
  let item;
  try {
    // 論点の名前を出すために一覧も読む
    [{ item }, B] = await Promise.all([api(`/api/bank/items/${encodeURIComponent(id)}`), B ? B : api("/api/bank/items")]);
  } catch (e) {
    if (gen !== itemGen) return;
    $("#bi-body").innerHTML = `<p class="muted">${esc(e.message)}</p>`;
    $("#bi-badges").innerHTML = $("#bi-meta").innerHTML = $("#bi-history").innerHTML = "";
    return;
  }
  if (gen !== itemGen) return;
  drawItem(item);
}

function drawItem(item) {
  I = item;
  const cname = Object.fromEntries((B ? B.concepts : []).map((c) => [c.concept, c.name]));
  $("#bi-badges").innerHTML = `<span class="badge">${esc(KIND_LABEL[item.kind] || item.kind || "?")}</span> ` +
    (item.trashed ? `<span class="badge ng">ゴミ箱</span>` : stateBadge(item));
  const list = (xs, f) => xs.map((x, i) => (i ? f(x) : `<b>${f(x)}</b>`)).join("、") || "—";
  $("#bi-meta").innerHTML = `分野：${list(item.tags, (t) => esc(`${tagName(t)}（${t}）`))}<br>
    論点：${list(item.concepts, (c) => esc(cname[c] ? `${cname[c]}（${c}）` : c))}<br>
    難しさ：${item.difficulty ? esc(`${item.difficulty}（${{ 1: "易しい", 2: "ふつう", 3: "難しい" }[item.difficulty]}）`) : "—"}
    ${item.meta.time_min ? `・目安 ${esc(item.meta.time_min)}分` : ""}
    ${item.created_at ? `<span class="muted">・作成 ${esc(item.created_at.slice(0, 16).replace("T", " "))}</span>` : ""}
    ${item.issued_to ? `<br>長文として出題済み：<code>${esc(item.issued_to)}</code>` : ""}`;
  const note = item.trashed ? "この問題はゴミ箱にあります。出題されません。"
    : item.busy ? "解いている途中のセットに入っているので、セットを提出するまで編集・削除できません。"
    : !item.has_key ? "正解キー（bank/keys/）がないので出題されません。"
    : !item.verified ? "確認待ちです。次の /prepare で verifier が確かめるまで出題されません。" : "";
  $("#bi-note").hidden = !note;
  $("#bi-note").textContent = note;
  $("#bi-body").innerHTML = md.render(item.body).html;
  $("#bi-history").innerHTML = item.history.length ? `<div class="table"><table><thead><tr><th>日時</th><th>結果</th><th>答え</th><th>セット</th></tr></thead><tbody>${
    item.history.slice().reverse().map((h) => `<tr><td>${esc(String(h.graded_at || "").slice(0, 16).replace("T", " "))}</td>
      <td>${h.max ? `${h.score} / ${h.max}` : "—"}${h.confidence ? ` <span class="sub">${esc({ sure: "確か", narrowed: "絞った", guess: "勘" }[h.confidence] || h.confidence)}</span>` : ""}</td>
      <td>${esc(h.answer || "")}${h.weak && h.weak.length ? `<div class="sub">${h.weak.map((w) => esc(String(w))).join("、")}</div>` : ""}</td>
      <td>${h.set ? `<a href="/sets/${encodeURIComponent(h.set)}">${esc(h.set.replace(/^set-/, ""))}</a>` : ""}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="muted">まだ解いていません。</p>`;
  const locked = item.trashed || item.busy;
  $("#bi-edit-card").hidden = item.trashed;
  $("#bi-edit-open").disabled = locked;
  $("#bi-trash").hidden = item.trashed;
  $("#bi-trash").disabled = item.busy;
  $("#bi-restore").hidden = !item.trashed;
}

async function fetchKey() {
  const { item } = await api(`/api/bank/items/${encodeURIComponent(I.id)}?key=1`);
  return item.key;
}

$("#bi-key-show").addEventListener("click", async () => {
  if (!I) return;
  try {
    const key = await fetchKey();
    $("#bi-key").innerHTML = key == null ? `<p class="muted">正解キーがありません。</p>` : renderKey(key);
    $("#bi-key").hidden = false;
    $("#bi-key-show").hidden = true;
    $("#bi-key-help").hidden = true;
  } catch (e) { notice(esc(e.message), "error"); }
});

function renderKey(text) {
  const r = md.render(text);
  const head = Object.entries(r.meta).filter(([k]) => k !== "id").map(([k, v]) => `<b>${esc(k)}</b>：${esc(v)}`).join("　");
  return (head ? `<p class="small">${head}</p>` : "") + r.html;
}

// ---------------------------------------------------------- 編集・削除
function closeEdit() {
  $("#bi-edit").hidden = true;
  $("#bi-edit-open").hidden = false;
  $("#bi-edit-status").textContent = "";
}

$("#bi-edit-open").addEventListener("click", async () => {
  if (!I) return;
  try {
    const key = await fetchKey();
    $("#bi-tags").value = I.tags.join(", ");
    $("#bi-concepts").value = I.concepts.join(", ");
    $("#bi-diff").value = String(I.difficulty || 2);
    $("#bi-body-text").value = I.body;
    $("#bi-key-text").value = key || "";
    $("#bi-key-text").dataset.orig = key || "";
    $("#bi-concept-list").innerHTML = (B ? B.concepts : []).map((c) => `<option value="${esc(c.concept)}">${esc(c.name)}</option>`).join("");
    $("#bi-edit").hidden = false;
    $("#bi-edit-open").hidden = true;
  } catch (e) { notice(esc(e.message), "error"); }
});
$("#bi-edit-cancel").addEventListener("click", closeEdit);

const splitList = (s) => s.split(/[,、\s]+/).map((x) => x.trim()).filter(Boolean);

$("#bi-edit").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!I) return;
  const body = { tags: splitList($("#bi-tags").value), concepts: splitList($("#bi-concepts").value), difficulty: Number($("#bi-diff").value) };
  if ($("#bi-body-text").value.trim() !== I.body.trim()) body.body = $("#bi-body-text").value;
  const key = $("#bi-key-text").value;
  if (key.trim() !== ($("#bi-key-text").dataset.orig || "").trim()) body.key = key;
  if ((body.body != null || body.key != null) && I.verified
    && !confirm("本文か正解キーを変えたので、この問題は「確認待ち」に戻り、次の /prepare で確かめるまで出題されません。保存しますか？")) return;
  $("#bi-edit-status").textContent = "保存中…";
  try {
    const { item } = await api(`/api/bank/items/${encodeURIComponent(I.id)}`, { method: "PUT", body });
    drawItem(item);
    closeEdit();
    $("#bi-key").hidden = true;
    $("#bi-key-show").hidden = false;
    $("#bi-key-help").hidden = false;
    notice("保存しました。");
  } catch (err) { $("#bi-edit-status").textContent = err.message; }
});

$("#bi-trash").addEventListener("click", async () => {
  if (!I || !confirm(`${I.id} をゴミ箱に移します。よろしいですか？（あとで戻せます）`)) return;
  try {
    await api(`/api/bank/items/${encodeURIComponent(I.id)}/trash`, { method: "POST", body: {} });
    notice(`${esc(I.id)} をゴミ箱に移しました。問題の一覧のゴミ箱から戻せます。`);
    go("/bank" + bankQuery);
  } catch (e) { notice(esc(e.message), "error"); }
});

$("#bi-restore").addEventListener("click", async () => {
  if (!I) return;
  try {
    await api(`/api/bank/items/${encodeURIComponent(I.id)}/restore`, { method: "POST", body: {} });
    notice(`${esc(I.id)} をバンクに戻しました。`);
    await loadBankItem(I.id);
  } catch (e) { notice(esc(e.message), "error"); }
});

// ---------------------------------------------------------- 論点
export async function loadConceptsPage(q) {
  const b = await api("/api/bank");
  const model = b.model || {};
  const stock = (b.stock && b.stock.concepts) || [];
  const tags = [...new Set(stock.map((r) => r.tag))];
  const cur = q.get("tag") || "";
  $("#concepts-tag").innerHTML = opt("", "分野：すべて", cur) + tags.map((t) => opt(t, `${t}：${tagName(t)}`, cur)).join("");
  $("#concepts-tag").hidden = tags.length < 2;
  if (!stock.length) {
    $("#concepts-summary").textContent = "";
    $("#concepts-body").innerHTML = `<p class="muted">論点の一覧（exam/concepts.md）がまだありません。ホームの「問題バンクを作る」から作れます。</p>`;
    $("#concepts-long").innerHTML = "";
    return;
  }
  const rows = stock.filter((r) => !cur || r.tag === cur).map((r) => ({ ...r, m: model[r.concept] || {} }));
  rows.sort((x, y) => (STATUS_ORDER[x.m.status] ?? 2) - (STATUS_ORDER[y.m.status] ?? 2) || (x.m.p ?? 1) - (y.m.p ?? 1));
  const need = rows.reduce((n, r) => n + Object.values(r.need).reduce((a, c) => a + c, 0), 0);
  $("#concepts-summary").textContent = `論点 ${rows.length}・確認待ち ${b.unverified}問` + (need ? `・足りない問題 ${need}問` : "");
  const kinds = Object.keys(rows[0]?.need || {});
  const stockCell = (r) => kinds.map((k) => {
    const have = (r.have || {})[k] || 0, n = r.need[k] || 0;
    return `${esc(KIND_LABEL[k] || k)} ${have}${n ? `<span class="need">（あと${n}）</span>` : ""}`;
  }).join("<br>");
  $("#concepts-body").innerHTML = `<div class="table"><table class="list"><thead><tr><th>論点</th><th>判定</th><th>見込み</th><th>解いた数</th><th>在庫</th></tr></thead><tbody>${
    rows.map((r) => `<tr class="st-${esc(r.m.status || "")}">
      <td><a href="/bank?concept=${encodeURIComponent(r.concept)}">${esc(r.name)}</a><div class="sub">${esc(r.concept)}</div></td>
      <td>${r.m.status ? `<span class="badge">${esc(r.m.status)}</span>` : "—"}</td>
      <td>${r.m.n ? `${pct(r.m.p)} <span class="sub">(${pct(r.m.low)}〜${pct(r.m.high)})</span>` : "—"}</td>
      <td>${r.m.n || 0}</td><td class="small">${stockCell(r)}</td></tr>`).join("")}</tbody></table></div>`;
  const long = ((b.stock && b.stock.long) || []).filter((r) => !cur || r.tag === cur);
  $("#concepts-long").innerHTML = long.length ? `<div class="table"><table><thead><tr><th>分野</th><th>まだ出していない長文</th></tr></thead><tbody>${
    long.map((r) => `<tr><td><a href="/bank?kind=long&tag=${encodeURIComponent(r.tag)}">${esc(tagName(r.tag))}</a> <span class="sub">${esc(r.tag)}</span></td>
      <td>${r.have}${r.need ? `<span class="need">（あと${r.need}）</span>` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">—</p>`;
}
$("#concepts-tag").addEventListener("change", () => {
  const t = $("#concepts-tag").value;
  history.replaceState(null, "", "/concepts" + (t ? "?tag=" + encodeURIComponent(t) : ""));
  loadConceptsPage(new URLSearchParams(location.search)).catch((e) => notice(esc(e.message), "error"));
});
