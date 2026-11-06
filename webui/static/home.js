// learn web UI の home.js：ホーム：おすすめ・論点の見込み・資格のサンプル、資料（refs/）のアップロード。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, api, esc, notice, st } from "./util.js";
import { setStudyPath, showTab } from "./main.js";
import { openJob, start } from "./study.js";
import { newSet, openSet } from "./set.js";
import { pct } from "./stats.js";
import { useExample } from "./settings.js";

// ---------------------------------------------------------- ホーム
export const STATUS_ORDER = { "苦手": 0, "ボーダー": 1, "測定中": 2, "未出題": 3, "得意": 4 };

let homeGen = 0;
export async function loadHome() {
  if (!st.S) return;
  const gen = ++homeGen; // 資格を切り替えたあとに古い応答が届いたら捨てる
  const ok = st.S.profile === "ok";
  $("#home-exam-name").textContent = ok ? st.S.exam || "いまの資格" : "まだ資格が選ばれていません";
  $("#home-actions").hidden = !ok;
  $("#home-tags-card").hidden = !ok;
  $("#home-rec").textContent = ok && st.S.recommend.written.length
    ? "おすすめの分野：" + st.S.recommend.written.map((r) => `${r.name}（${r.reason}）`).join("、") : "";
  renderExamples();
  api("/api/glossary").then((g) => {
    const todo = g.terms.filter((x) => !x.known).length;
    $("#home-glossary").hidden = !todo;
    $("#home-glossary").innerHTML = `単語帳：まだ覚えていない言葉が ${todo}語あります。<a href="/glossary">単語帳を開く</a>`;
  }).catch(() => {});
  $("#home-set").hidden = !ok;
  if (!ok) { $("#home-summary").textContent = "下の「資格を選ぶ」から始めてください"; $("#home-continue").innerHTML = ""; return; }
  loadHomeSet(gen).catch((e) => { $("#home-set-text").textContent = e.message; });

  const [{ analysis: a }, { items }, { jobs }] = await Promise.all([api("/api/analysis"), api("/api/items"), api("/api/jobs")]);
  if (gen !== homeGen) return;
  $("#home-summary").textContent = a
    ? `記述 ${a.attempts.written}回（平均 ${pct(a.overall.written_avg)}）・択一 ${a.attempts.drill}問（正答率 ${pct(a.overall.drill_avg)}）・合格ライン ${pct(a.pass_ratio)}` : "";

  // 書きかけ（解答はあるが未採点）の問題。出題した会話が残っていれば、そこから続ける
  const open = items.filter((x) => x.kind === "written" && !x.graded)
    .map((x) => ({ x, j: jobs.find((j) => (j.question_ids || []).includes(x.id)) }))
    .filter((o) => o.j).slice(0, 3);
  $("#home-continue").innerHTML = open.length
    ? `<h3>続きから</h3><div class="row">${open.map((o) => `<button data-job="${esc(o.j.id)}">${esc(o.x.id)}${o.x.has_answer ? "（書きかけ）" : ""}</button>`).join("")}</div>` : "";

  const tags = st.S.tags.map((t) => ({ ...t, a: (a && a.tags[t.tag]) || {} }))
    .sort((x, y) => (STATUS_ORDER[x.a.status] ?? 2) - (STATUS_ORDER[y.a.status] ?? 2) || y.weight - x.weight);
  $("#home-tags").innerHTML = tags.map((t) => `<div class="tag-card st-${esc(t.a.status || "")}">
      <div><b>${esc(t.name)}</b> <span class="muted small">${esc(t.tag)}</span></div>
      <div class="small">${t.a.status ? `<span class="badge">${esc(t.a.status)}</span> ` : ""}<span class="muted">記述 ${pct(t.a.written && t.a.written.p)}・択一 ${pct(t.a.drill && t.a.drill.p)}</span></div>
      <div class="row"><button class="small" data-start="question" data-tag="${esc(t.tag)}">記述を1問</button><button class="small" data-start="drill" data-tag="${esc(t.tag)}">択一</button></div>
    </div>`).join("");
}

async function loadHomeSet(gen) {
  const [b, { sets }] = await Promise.all([api("/api/bank"), api("/api/sets")]);
  if (gen !== homeGen) return;
  const model = Object.values(b.model || {});
  const measured = model.filter((c) => !["未出題", "測定中"].includes(c.status)).length;
  const weak = model.filter((c) => c.status === "苦手").sort((x, y) => x.p - y.p).slice(0, 3);
  $("#home-set-meta").textContent = b.ready ? `論点 ${b.concepts}・問題 ${b.items}問（択一 ${(b.kinds || {}).choice || 0}・記述 ${(b.kinds || {}).short || 0}・長文 ${(b.kinds || {}).long || 0}）` : "";
  $("#home-set-text").innerHTML = !b.ready
    ? (b.concepts ? "問題バンクの問題がまだ確認中です。もう少し待つか、作り足してください。" : "問題バンクがまだありません。分野ごとに論点を分け、問題を前もって作っておくと、苦手を機械的に洗い出して埋めるセットが出せます。")
    : (measured < model.length * 0.7
      ? `いまは<b>洗い出し</b>の段階です（測れた論点 ${measured} / ${model.length}）。まだ測れていない論点から出します。`
      : `いまは<b>穴埋め</b>の段階です。苦手な論点を中心に、間違えた問題のやり直しも混ぜて出します。`) +
      (weak.length ? `<br>苦手：${weak.map((c) => `${esc(c.name)}（${pct(c.p)}）`).join("、")}` : "") +
      (b.need ? `<br><span class="muted">足りない問題が ${b.need}問あります。</span>` : "");
  $("#home-set-start").hidden = !b.ready;
  $("#home-long").hidden = !b.ready;
  $("#home-prepare").hidden = b.ready && !b.need;
  $("#home-prepare").textContent = b.ready ? `問題を作り足す（${b.need}問）` : "問題バンクを作る";
  const open = sets.filter((s) => s.status === "open").slice(0, 3);
  $("#home-set-open").innerHTML = open.length
    ? `<div class="row">${open.map((s) => `<button data-set="${esc(s.id)}">続きから：${esc(s.id)}（${s.answered} / ${s.n}）</button>`).join("")}</div>` : "";
}
$("#home-set-start").addEventListener("click", () => newSet());
$("#home-prepare").addEventListener("click", () => start({ action: "prepare" }));

function renderExamples() {
  $("#home-examples").innerHTML = st.S.examples.map((e) => {
    const cur = st.S.profile === "ok" && st.S.exam === e.name;
    return `<div class="exam-card${cur ? " current" : ""}"><b>${esc(e.name)}</b>
      ${cur ? `<span class="badge">勉強中</span>` : `<button class="small" data-example="${esc(e.key)}">この資格で勉強する</button>`}</div>`;
  }).join("") || `<p class="muted small">用意してある資格はまだありません。</p>`;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-start], [data-example], [data-job], [data-set]");
  if (!b) return;
  e.preventDefault();
  if (b.dataset.set) return openSet(b.dataset.set);
  if (b.dataset.start === "drill") return newSet({ kind: "choice", tag: b.dataset.tag || "", n: Math.min(30, st.S ? st.S.drill_count : 10) });
  if (b.dataset.start) return start({ action: b.dataset.start, tag: b.dataset.tag || "" });
  if (b.dataset.example) return useExample(b.dataset.example);
  if (b.dataset.job) {
    try {
      const { jobs } = await api("/api/jobs");
      const j = jobs.find((x) => x.id === b.dataset.job);
      if (!j) return notice("この会話はもうありません", "warn");
      $("#set-card").hidden = true;
      setStudyPath(null);
      openJob(j);
      showTab("study");
    } catch (err) { notice(esc(err.message), "error"); }
  }
});

$("#home-setup").addEventListener("submit", (e) => {
  e.preventDefault();
  start({ action: "setup", exam: e.target.exam.value.trim() });
});

// ---------------------------------------------------------- 資料（refs/）
const size = (n) => (n >= 1 << 20 ? (n / (1 << 20)).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

export async function loadRefs() {
  const { refs } = await api("/api/refs");
  $("#refs-list").innerHTML = refs.length ? refs.map((r) => `<tr><td>${esc(r.path)}</td><td>${size(r.size)}</td>
    <td class="small">${new Date(r.mtime * 1000).toLocaleString()}</td></tr>`).join("")
    : `<tr><td colspan="3" class="muted">まだありません</td></tr>`;
}

async function uploadOne(dir, file, overwrite) {
  const q = `dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(file.name)}` + (overwrite ? "&overwrite=1" : "");
  const r = await fetch("/api/refs?" + q, { method: "POST", headers: { "X-Learn": "1", "Content-Type": "application/octet-stream" }, body: file });
  const data = await r.json().catch(() => ({}));
  if (r.status === 409 && !overwrite) {
    if (confirm(`${file.name} はすでにあります。上書きしますか？`)) return uploadOne(dir, file, true);
    return null;
  }
  if (!r.ok) throw new Error(`${file.name}：${data.error || r.statusText}`);
  return data.path;
}

$("#upload").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const files = Array.from(f.files.files);
  if (!files.length) return notice("ファイルを選んでください", "warn");
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  const done = [];
  try {
    for (const [i, file] of files.entries()) {
      $("#upload-status").textContent = `アップロード中… ${i + 1} / ${files.length}`;
      const p = await uploadOne(f.dir.value, file);
      if (p) done.push(p);
    }
    f.files.value = "";
    $("#dropzone span").textContent = "ここにファイルをドロップ、またはクリックして選ぶ（50MB まで）";
    $("#upload-status").textContent = done.length ? `保存しました：${done.join("、")}` : "";
  } catch (err) {
    $("#upload-status").textContent = done.length ? `保存しました：${done.join("、")}` : "";
    notice(esc(err.message), "error");
  } finally {
    btn.disabled = false;
    loadRefs().catch(() => {});
  }
});

const dz = $("#dropzone");
const showPicked = () => {
  const fs = Array.from($("#upload").files.files);
  dz.querySelector("span").textContent = fs.length ? fs.map((x) => `${x.name}（${size(x.size)}）`).join("、") : "ここにファイルをドロップ、またはクリックして選ぶ（50MB まで）";
};
$("#upload").files.addEventListener("change", showPicked);
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
dz.addEventListener("dragleave", () => dz.classList.remove("over"));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  dz.classList.remove("over");
  $("#upload").files.files = e.dataTransfer.files;
  showPicked();
});

$("#import-form").addEventListener("submit", (e) => {
  e.preventDefault();
  start({ action: "import", year: e.target.year.value.trim(), q: e.target.q.value.trim() });
});
