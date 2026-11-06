// learn web UI の stats.js：成績。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, api, esc } from "./util.js";

// ---------------------------------------------------------- 成績
export const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
const MISS = { knowledge: "知識", reading: "読解", writing: "記述" };

export async function loadStats() {
  const box = $("#stats-body");
  try {
    const { analysis: a } = await api("/api/analysis");
    if (!a) { box.innerHTML = "<p>試験が設定されていません。</p>"; return; }
    const fmt = (f) => (f ? `${pct(f.p)} <span class="muted small">(${f.n}件${f.n_sub ? `＋副${f.n_sub}` : ""}${f.trend_label ? " " + esc(f.trend_label) : ""})</span>` : "—");
    const rows = Object.entries(a.tags).map(([tag, t]) => `<tr class="st-${esc(t.status)}">
      <td><b>${esc(tag)}</b><div class="muted small">${esc(t.name)}</div></td>
      <td><span class="badge">${esc(t.status)}</span>${t.sure ? "<div class=\"small\">確か</div>" : ""}</td>
      <td>${fmt(t.written)}</td><td>${fmt(t.drill)}</td>
      <td class="small">${t.main_miss ? esc(MISS[t.main_miss] || t.main_miss) : ""}${t.gap_label ? "<div>" + esc(t.gap_label) + "</div>" : ""}${t.weak && t.weak.length ? "<div class=\"muted\">" + t.weak.slice(0, 3).map((w) => esc(String(w))).join("、") + "</div>" : ""}</td></tr>`).join("");
    const miss = Object.entries(a.miss_type || {}).map(([k, v]) => `${esc(MISS[k] || k)} ${v}件`).join("、") || "—";
    box.innerHTML = `<p>記述式 ${a.attempts.written}回（平均 ${pct(a.overall.written_avg)}）・択一 ${a.attempts.drill}問（正答率 ${pct(a.overall.drill_avg)}）・合格ライン ${pct(a.pass_ratio)}</p>
      <p>ミスの種類：${miss}</p>
      ${(a.repeated_weak || []).length ? `<p>繰り返している弱点：${a.repeated_weak.slice(0, 5).map((w) => `${esc(w.term)}（${w.count}回）`).join("、")}</p>` : ""}
      <div class="table"><table><thead><tr><th>分野</th><th>判定</th><th>記述の見込み</th><th>択一の見込み</th><th>原因・弱点</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${askSection(a)}
      <p class="muted small">詳しい原因の読み解きと次の一手は「学習する」の「振り返り」で Claude が出します。</p>`;
  } catch (e) { box.textContent = e.message; }
}

const ASK_LABEL = { word: "言葉の意味", ask: "何を聞かれているか", worry: "不安・その他" };
function askSection(a) {
  const q = a.asks || {};
  if (!q.total) return "";
  const kinds = Object.entries(q.by_kind || {}).map(([k, v]) => `${esc(ASK_LABEL[k] || k)} ${v}件`).join("、");
  const tags = Object.entries(a.tags).filter(([, t]) => t.asks)
    .map(([tag, t]) => [tag, t, Object.values(t.asks).reduce((x, y) => x + y, 0)]).sort((x, y) => y[2] - x[2]).slice(0, 5)
    .map(([tag, t, n]) => `${esc(t.name || tag)} ${n}件`).join("、");
  const terms = (q.repeated_terms || []).slice(0, 5).map((w) => `${esc(w.term)}（${w.count}回）`).join("、");
  return `<h3>解答中の質問</h3>
    <p class="small">${kinds}${tags ? `<br>分野別：${tags}` : ""}${terms ? `<br>何度も聞いた言葉：${terms}` : ""}</p>
    <p class="muted small">質問しても点数や判定は変わりません。言葉の質問が多い分野は、択一ドリルと<a href="/glossary">単語帳</a>で知識を固めるのがおすすめです。</p>`;
}
