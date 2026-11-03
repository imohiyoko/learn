// learn web UI の history.js：履歴（セットと出題済みの問題）。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, $$, api, esc } from "./util.js";

// ---------------------------------------------------------- 履歴
async function loadSets() {
  const { sets } = await api("/api/sets");
  const st = { open: "解いている途中", submitted: "採点中", graded: "レポートあり" };
  $("#sets-list").innerHTML = sets.length ? sets.map((s) => `<tr data-set="${esc(s.id)}"><td><a href="#">${esc(s.id)}</a></td>
    <td>${esc(s.phase || "")}</td><td>その場で採点 ${s.correct} / ${s.choices}</td><td>${st[s.status] || s.status}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">まだセットを解いていません</td></tr>`;
}

export async function loadItems() {
  loadSets().catch(() => {});
  const { items } = await api("/api/items");
  $("#items").innerHTML = items.length ? items.map((x) => `<tr data-id="${esc(x.id)}">
    <td><a href="#">${esc(x.id)}</a></td><td>${x.kind === "drill" ? "択一" : "記述"}</td><td>${esc(x.tags)}</td>
    <td>${x.score ? `${x.score[0]} / ${x.score[1]}` : x.graded ? "採点済み" : "<span class=\"muted\">未採点</span>"}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">まだ問題がありません</td></tr>`;
}
$("#items").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  e.preventDefault();
  openViewer(tr.dataset.id, "questions");
});

async function openViewer(id, kind) {
  $("#viewer").hidden = false;
  $("#viewer-title").textContent = id;
  const names = { questions: "問題", answers: "解答", results: "講評" };
  $("#viewer-tabs").innerHTML = Object.entries(names).map(([k, n]) => `<button class="small ${k === kind ? "active" : ""}" data-k="${k}">${n}</button>`).join("");
  $$("#viewer-tabs button").forEach((b) => b.addEventListener("click", () => openViewer(id, b.dataset.k)));
  try {
    const f = await api(`/api/file?kind=${kind}&id=${encodeURIComponent(id)}`);
    const r = md.render(f.text);
    $("#viewer-body").innerHTML = kind === "questions" && r.meta.verified === "false"
      ? `<p class="muted">この問題は検証が終わるまで表示しません。</p>` : r.html;
  } catch (e) { $("#viewer-body").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  $("#viewer").scrollIntoView({ behavior: "smooth" });
}
