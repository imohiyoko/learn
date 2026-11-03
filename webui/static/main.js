// learn web UI の main.js：タブ・状態の読み込み・起動。入口（index.html から読む）。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, $$, api, esc, notice, st } from "./util.js";
import { openJob, timerRender, updateRec } from "./study.js";
import { loadHome, loadRefs } from "./home.js";
import { loadStats } from "./stats.js";
import { loadGlossary } from "./glossary.js";
import { loadItems } from "./history.js";
import { loadProfile } from "./settings.js";

// ---------------------------------------------------------- タブ
export function showTab(name) {
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
  try { localStorage.setItem("learn.tab", name); } catch (e) {}
  if (name === "home") loadHome().catch((e) => notice(esc(e.message), "error"));
  if (name === "stats") loadStats();
  if (name === "history") loadItems();
  if (name === "glossary") loadGlossary().catch((e) => notice(esc(e.message), "error"));
  if (name === "refs") loadRefs().catch((e) => notice(esc(e.message), "error"));
  if (name === "settings") loadProfile();
  timerRender();
}
$$("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------------------------------------------------------- 状態
export async function loadState() {
  st.S = await api("/api/state");
  $("#exam").textContent = st.S.exam || "";
  if (st.S.profile !== "ok") {
    notice(st.S.profile === "missing"
      ? "まだ資格が選ばれていません。<a href=\"#\" data-go=\"home\">ホーム</a>の「資格を選ぶ」から選んでください。"
      : "<code>exam/profile.md</code> に TODO が残っています。<a href=\"#\" data-go=\"settings\">設定</a>で埋めてください。", "warn");
  } else if (st.S.runner === "cli" && !st.S.claude) {
    notice("Claude Code（<code>claude</code> コマンド）が見つかりません。出題・採点には Claude Code のインストールとログインが必要です。", "warn");
  } else notice("");

  const sel = $("#tag-select");
  const keep = sel.value;
  const recW = st.S.recommend.written.map((r) => r.tag);
  sel.innerHTML = `<option value="">おまかせ（弱点から自動）</option>` +
    st.S.tags.map((t) => `<option value="${esc(t.tag)}">${esc(t.tag)}：${esc(t.name)}</option>`).join("");
  sel.value = keep;
  $("#drill-count").placeholder = st.S.drill_count;
  $("#example-select").innerHTML = st.S.examples.map((e) => `<option value="${esc(e.key)}">${esc(e.name)}</option>`).join("");
  $("#example-select").hidden = $("#copy-example").hidden = !st.S.examples.length;
  updateRec();
  return recW;
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-go]");
  if (a) { e.preventDefault(); showTab(a.dataset.go); }
});

// ---------------------------------------------------------- 起動
(async function init() {
  try { await loadState(); } catch (e) { notice(esc(e.message), "error"); return; }
  let tab = "home";
  try { tab = localStorage.getItem("learn.tab") || tab; } catch (e) {}
  if (st.S.profile !== "ok" || !$("#tab-" + tab)) tab = "home";
  showTab(tab);
  // 再読み込みしても、最後の会話に戻れるように
  try {
    const { jobs } = await api("/api/jobs");
    let last = null;
    try { last = localStorage.getItem("learn.job"); } catch (e) {}
    // 実行中の会話を優先する（別の端末で始めたものも含めて）
    const running = jobs.find((x) => x.status === "running");
    const j = running || jobs.find((x) => x.id === last);
    if (j) openJob(j);
    if (running && running.id !== last) showTab("study");
  } catch (e) {}
})();
