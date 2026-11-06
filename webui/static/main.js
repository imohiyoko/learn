// learn web UI の main.js：タブ・状態の読み込み・起動。入口（index.html から読む）。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, $$, api, esc, notice, st } from "./util.js";
import { openJob, timerRender, updateRec } from "./study.js";
import { loadHome, loadRefs } from "./home.js";
import { loadStats } from "./stats.js";
import { loadGlossary } from "./glossary.js";
import { loadItems } from "./history.js";
import { loadProfile } from "./settings.js";
import { openSet } from "./set.js";
import { loadBank, loadBankItem, loadConceptsPage } from "./bank.js";

// ---------------------------------------------------------- ページ
// URL ごとにページを分ける（/bank/<id> など）。サーバーはどのページの URL にも index.html を返すので、
// ここで URL を見て出すページを決める。戻る・ブックマーク・再読み込みが効く
const PATHS = { home: "/", study: "/study", bank: "/bank", concepts: "/concepts", stats: "/stats",
  history: "/history", glossary: "/glossary", refs: "/refs", settings: "/settings" };

export function parsePath(path) {
  path = path.replace(/\/+$/, "") || "/";
  let m;
  if ((m = path.match(/^\/bank\/([^/]+)$/))) return { page: "bank-item", nav: "bank", id: decodeURIComponent(m[1]) };
  if ((m = path.match(/^\/sets\/([^/]+)$/))) return { page: "study", nav: "study", set: decodeURIComponent(m[1]) };
  const name = path === "/" ? "home" : path.slice(1);
  return PATHS[name] ? { page: name, nav: name } : { page: "home", nav: "home" };
}

function display(r) {
  $$("nav a[data-page]").forEach((a) => a.classList.toggle("active", a.dataset.page === r.nav));
  $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + r.page));
  const err = (e) => notice(esc(e.message), "error");
  const q = new URLSearchParams(location.search);
  if (r.page === "home") loadHome().catch(err);
  if (r.page === "stats") loadStats();
  if (r.page === "history") loadItems();
  if (r.page === "glossary") loadGlossary().catch(err);
  if (r.page === "refs") loadRefs().catch(err);
  if (r.page === "settings") loadProfile();
  if (r.page === "bank") loadBank(q).catch(err);
  if (r.page === "bank-item") loadBankItem(r.id).catch(err);
  if (r.page === "concepts") loadConceptsPage(q).catch(err);
  if (r.set && (!st.SET || st.SET.id !== r.set)) openSet(r.set, true);
  timerRender();
}

// URL を変えてページを出す。replace なら履歴を増やさない
export function go(url, opts = {}) {
  if (url !== location.pathname + location.search) history[opts.replace ? "replaceState" : "pushState"](null, "", url);
  display(parsePath(location.pathname));
  if (!opts.keepScroll) window.scrollTo({ top: 0 });
}

// 以前のタブ名でページを出す（ほかのモジュールから呼ぶ）。いまの URL がそのページのもの（/sets/<id> など）ならそのまま
export function showTab(name) {
  const cur = parsePath(location.pathname);
  if (cur.page === name) return display(cur);
  go(PATHS[name] || "/", { keepScroll: true });
}

// セットを開いたら /sets/<id>、しまったら /study に（履歴は増やさない）
export function setStudyPath(sid) {
  const cur = parsePath(location.pathname);
  if (cur.page !== "study") return;
  const want = sid ? "/sets/" + encodeURIComponent(sid) : "/study";
  if (location.pathname !== want) history.replaceState(null, "", want);
}

window.addEventListener("popstate", () => display(parsePath(location.pathname)));

// ページ内のリンク（/bank など）は読み込み直さずに切り替える
document.addEventListener("click", (e) => {
  const old = e.target.closest("[data-go]");
  if (old) { e.preventDefault(); showTab(old.dataset.go); return; }
  const a = e.target.closest("a[href^='/']");
  if (!a || a.target || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const href = a.getAttribute("href");
  if (/^\/(api|assets)\//.test(href) || href.startsWith("//")) return;
  e.preventDefault();
  go(href);
});

// ---------------------------------------------------------- 状態
export async function loadState() {
  st.S = await api("/api/state");
  $("#exam").textContent = st.S.exam || "";
  if (st.S.profile !== "ok") {
    notice(st.S.profile === "missing"
      ? "まだ資格が選ばれていません。<a href=\"/\">ホーム</a>の「資格を選ぶ」から選んでください。"
      : "<code>exam/profile.md</code> に TODO が残っています。<a href=\"/settings\">設定</a>で埋めてください。", "warn");
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

// ---------------------------------------------------------- 起動
(async function init() {
  try { await loadState(); } catch (e) { notice(esc(e.message), "error"); return; }
  let r = parsePath(location.pathname);
  // 資格がまだ決まっていなければ、ホームで選んでもらう
  if (st.S.profile !== "ok" && r.page !== "settings") { history.replaceState(null, "", "/"); r = parsePath("/"); }
  display(r);
  // 再読み込みしても、最後の会話に戻れるように
  try {
    const { jobs } = await api("/api/jobs");
    let last = null;
    try { last = localStorage.getItem("learn.job"); } catch (e) {}
    // 実行中の会話を優先する（別の端末で始めたものも含めて）
    const running = jobs.find((x) => x.status === "running");
    const j = running || jobs.find((x) => x.id === last);
    if (j) openJob(j);
    // 実行中の会話があれば学習のページへ（ほかのページを開いたときは、そのページのまま）
    if (running && running.id !== last && ["home", "study"].includes(parsePath(location.pathname).page)) showTab("study");
  } catch (e) {}
})();
