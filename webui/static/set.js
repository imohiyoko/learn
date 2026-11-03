// learn web UI の set.js：セット（問題バンク）：解く・提出・レポート、途中のわからない言葉。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, $$, api, esc, notice, st } from "./util.js";
import { showTab } from "./main.js";
import { fetchShown, openJob, start, timerAutoStart, timerPause, timerSet, tm } from "./study.js";
import { pct } from "./stats.js";

// ---------------------------------------------------------- セット（問題バンク）
// scripts/route.py が選んだ問題を1問ずつ。択一は答えたその場でサーバーが採点して解説を出す。
// 記述は書いて保存し、最後に提出すると /grade-set が採点とレポートを作る
let setAt = 0, setPick = null, setUnsure = new Set(), setGen = 0;
const KIND_LABEL = { choice: "択一", fill: "穴埋め", short: "記述" };
const AUTO = (it) => it.kind === "choice" || it.kind === "fill"; // 答えたその場で採点する形式
const SLOT_HELP = { "洗い出し": "まだ測れていない論点", "穴埋め": "苦手な論点", "やり直し": "前に間違えた問題", "確認": "得意な論点の確認" };

export async function newSet(opts) {
  const body = { n: 10, ...(opts || {}) };
  try {
    const { set } = await api("/api/sets", { method: "POST", body });
    showSet(set);
    // 在庫だけで出す。足りなければ知らせる（バンクは「問題バンクを作る」で好きなときに作り足す）
    if (set.items.length < body.n) {
      notice(`問題バンクの在庫が足りないので、${set.items.length}問だけ出しました。` +
        `「問題バンクを作る」で作り足すと、選べる問題が増えます。`, "warn");
    }
    showTab("study");
    window.scrollTo({ top: 0 });
  } catch (e) { notice(esc(e.message), "error"); }
}

export async function openSet(id, keepTab) {
  const gen = ++setGen;
  try {
    const { set } = await api(`/api/sets/${encodeURIComponent(id)}`);
    if (gen !== setGen) return;
    showSet(set);
    if (!keepTab) { showTab("study"); window.scrollTo({ top: 0 }); }
  } catch (e) { notice(esc(e.message), "error"); }
}

function showSet(s, at) {
  const same = st.SET && st.SET.id === s.id;
  st.SET = s;
  try { localStorage.setItem("learn.set", s.id); } catch (e) {}
  $("#set-card").hidden = false;
  $("#set-title").textContent = (s.kind === "choice" ? "択一ドリル " : "セット ") + s.id.replace(/^set-/, "");
  const label = { open: "解いている途中", submitted: "採点中", graded: "採点済み" }[s.status];
  $("#set-meta").textContent = `${s.phase}・${s.items.length}問・${label}`;
  const first = s.items.findIndex((it) => it.answer == null);
  setAt = at ?? (same ? setAt : first < 0 ? s.items.length : first);
  if (s.status === "open") timerSet(s.id, s.items.reduce((m, it) => m + (parseFloat(it.time_min) || (AUTO(it) ? 1 : 8)), 0));
  else if (tm.id === s.id) timerSet(null);
  drawSet();
}

function drawSet() {
  const s = st.SET;
  const graded = s.status === "graded";
  $("#set-dots").innerHTML = s.items.map((it, i) => {
    const r = it.result;
    const cls = r ? (r.correct ? "ok" : "ng") : it.answer != null ? "done" : "";
    return `<button type="button" data-si="${i}" class="${cls}${i === setAt ? " cur" : ""}" title="${esc(it.concept_names.join("、"))}">${i + 1}</button>`;
  }).join("") + (s.status === "open" ? `<button type="button" data-si="${s.items.length}" class="${setAt >= s.items.length ? "cur" : ""}" title="提出">✓</button>` : "");
  $("#set-report").hidden = !graded;
  $("#set-item").hidden = graded || setAt >= s.items.length;
  $("#set-finish").hidden = graded || setAt < s.items.length;
  if (graded) return drawReport();
  if (setAt >= s.items.length) return drawFinish();
  const it = s.items[setAt];
  setPick = null;
  setUnsure = new Set();
  $("#set-reason").textContent = `${setAt + 1} / ${s.items.length}・${KIND_LABEL[it.kind] || "記述"}・${it.slot}（${SLOT_HELP[it.slot] || ""}）・${it.reason}`;
  $("#set-body").innerHTML = md.render(it.body).html;
  const answered = AUTO(it) && !!it.result;
  const right = it.key && it.key.answer;
  $("#set-options").hidden = it.kind !== "choice";
  $("#set-short").hidden = it.kind !== "short";
  $("#set-fill").hidden = it.kind !== "fill";
  if (it.kind === "fill") {
    const res = (it.result && it.result.blanks) || {};
    const locked = answered || s.status !== "open";
    $("#set-fill").innerHTML = (it.blanks || []).map((x) => {
      const r = res[x];
      const val = r ? r.answer : (it.answer && it.answer[x]) || "";
      const mark = r ? `<span class="mark ${r.ok ? "ok" : "ng"}">${r.ok ? "○" : "×"}</span>` : "";
      return `<label class="fill-row"><span class="lbl">［${esc(x)}］</span><input data-blank="${esc(x)}" maxlength="200" value="${esc(val)}" aria-label="空欄 ${esc(x)}"${locked ? " disabled" : ""}>${mark}</label>`;
    }).join("");
  }
  if (it.kind === "choice") {
    const marked = new Set((it.result && it.result.unsure) || []);
    const locked = answered || s.status !== "open";
    $("#set-options").innerHTML = it.options.map((o) => {
      const cls = answered ? (o.label === right ? "right" : o.label === it.answer ? "wrong" : "") : "";
      const on = marked.has(o.label);
      return `<div class="opt-row"><button type="button" data-opt="${esc(o.label)}" class="${cls}"${locked ? " disabled" : ""}><span class="lbl">${esc(o.label)}</span><span>${md.render(o.text).html.replace(/^<p>|<\/p>$/g, "")}</span></button>`
        + `<button type="button" class="unsure${on ? " on" : ""}" data-unsure="${esc(o.label)}" aria-pressed="${on}" aria-label="${esc(o.label)} に自信がない" title="自信がない"${locked ? " disabled" : ""}>？</button></div>`;
    }).join("");
  } else {
    $("#set-short-text").value = it.answer || "";
    $("#set-short-text").disabled = s.status !== "open";
    shortCount();
  }
  const fb = $("#set-feedback");
  fb.hidden = !answered;
  if (answered) {
    fb.className = "feedback md " + (it.result.correct ? "ok" : "ng");
    const u = it.result.unsure || [];
    const confNote = !u.length ? ""
      : `<p class="small">「？」を付けた選択肢：${u.map(esc).join("・")}。${it.result.correct && u.includes(it.answer) ? "自信のないまま当たった問題です。" : ""}解説で、それぞれが正しい・誤りである理由を言えるか確かめましょう。</p>`;
    const head = it.kind === "fill"
      ? `<p><b>${it.result.correct ? "正解" : "不正解"}</b>（空欄 ${it.result.score} / ${it.result.max} 正解）</p>`
      : `<p><b>${it.result.correct ? "正解" : "不正解"}</b>（あなたの答え：${esc(it.answer)}・正解：${esc(right || "")}）</p>`;
    fb.innerHTML = head + confNote + md.render(it.key ? it.key.body : "").html;
  }
  $("#set-unsure-help").hidden = it.kind !== "choice" || s.status !== "open" || answered;
  $("#set-answer").hidden = s.status !== "open" || answered;
  $("#set-answer").textContent = AUTO(it) ? "答える" : "保存して次へ";
  $("#set-answer").disabled = AUTO(it);
  $("#set-prev").disabled = setAt === 0;
  $("#set-next").textContent = setAt === s.items.length - 1 ? (s.status === "open" ? "提出へ" : "次へ") : "次へ";
  $("#set-status").textContent = it.kind === "short" ? "記述は提出後にまとめて採点します" : "";
  drawSetAsk();
}

// ---------------------------------------------------------- セットの途中で、わからない言葉を聞く
// サーバーが記録し（log/asks.jsonl。点数には入れない）、/glossary で意味を作る。届いたら単語帳から拾って表示する。
// 答える前の択一で選択肢に出てくる言葉は、意味がそのまま答えになりうるので、答えたあとに回す
const setAsks = {}; // {問題id: [{term, state: wait|deferred|busy|late|done, meaning}]}
const termKey = (s) => String(s).normalize("NFKC").replace(/\s+/g, "").toLowerCase();
const ASK_STATE = {
  wait: "意味を作っています…",
  deferred: "選択肢に出てくる言葉なので、答えたあとに意味を出します",
  busy: "Claude がほかの作業中です。言葉は単語帳に入れたので、あとで「意味を書いてもらう」から作れます",
  late: "まだ届いていません。届いたら単語帳で見られます",
};
function drawSetAsk() {
  const it = st.SET && st.SET.items[setAt];
  if (!it) return;
  $("#set-ask-out").innerHTML = (setAsks[it.id] || []).map((a) =>
    `<p><b>${esc(a.term)}</b>：${a.meaning ? esc(a.meaning) : esc(ASK_STATE[a.state] || "")}</p>`).join("");
}
function applyAsk(a, r) {
  if (r.meaning) Object.assign(a, { meaning: r.meaning, state: "done" });
  else a.state = r.deferred ? "deferred" : r.busy ? "busy" : "wait";
  drawSetAsk();
  if (a.state === "wait") watchMeaning(a);
}
async function watchMeaning(a) {
  const key = termKey(a.term);
  for (let i = 0; i < 45; i++) {
    await new Promise((ok) => setTimeout(ok, 4000));
    try {
      const g = await api("/api/glossary");
      const t = g.terms.find((x) => x.meaning && x.key === key) || g.terms.find((x) => x.meaning && x.key && x.key.startsWith(key));
      if (t) { Object.assign(a, { meaning: t.meaning, state: "done" }); drawSetAsk(); return; }
    } catch (e) { /* 次の回にもう一度 */ }
  }
  a.state = "late";
  drawSetAsk();
}
$("#set-ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const it = st.SET && st.SET.items[setAt];
  const term = e.target.term.value.trim();
  if (!it || !term) return;
  try {
    const r = await api(`/api/sets/${st.SET.id}/ask`, { method: "POST", body: { item: it.id, term } });
    e.target.term.value = "";
    const a = { term };
    (setAsks[it.id] = setAsks[it.id] || []).push(a);
    applyAsk(a, r);
  } catch (err) { notice(esc(err.message), "error"); }
});
// 答えたら、答えのあとに回した言葉の意味を作る
function resumeDeferred(sid, iid) {
  for (const a of setAsks[iid] || []) {
    if (a.state !== "deferred") continue;
    api(`/api/sets/${sid}/ask`, { method: "POST", body: { item: iid, term: a.term, again: true } })
      .then((r) => applyAsk(a, r)).catch((err) => notice(esc(err.message), "error"));
  }
}

function shortCount() {
  const it = st.SET && st.SET.items[setAt];
  if (!it || it.kind !== "short") return;
  const m = (it.body || "").match(/(\d+)\s*字以内/);
  const lim = m ? +m[1] : null;
  const n = answer.count($("#set-short-text").value);
  const c = $("#set-short-count");
  c.textContent = lim ? (n > lim ? `${n} / ${lim}字（${n - lim}字オーバー）` : `${n} / ${lim}字`) : `${n}字`;
  c.className = "ans-count" + (lim && n > lim ? " over" : lim && n >= lim * 0.9 ? " near" : "");
}
$("#set-short-text").addEventListener("input", () => { shortCount(); timerAutoStart(); });

$("#set-options").addEventListener("click", (e) => {
  const u = e.target.closest("[data-unsure]");
  if (u) {
    if (u.disabled) return;
    const on = !setUnsure.has(u.dataset.unsure);
    if (on) setUnsure.add(u.dataset.unsure); else setUnsure.delete(u.dataset.unsure);
    u.classList.toggle("on", on);
    u.setAttribute("aria-pressed", String(on));
    timerAutoStart();
    return;
  }
  const b = e.target.closest("[data-opt]");
  if (!b || b.disabled) return;
  setPick = b.dataset.opt;
  $$("#set-options [data-opt]").forEach((x) => x.classList.toggle("sel", x === b));
  $("#set-answer").disabled = false;
  timerAutoStart();
});

function fillValue() {
  return Object.fromEntries($$("#set-fill [data-blank]").map((x) => [x.dataset.blank, x.value]));
}
$("#set-fill").addEventListener("input", () => {
  $("#set-answer").disabled = !Object.values(fillValue()).some((v) => v.trim());
  timerAutoStart();
});
$("#set-fill").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.isComposing) return;
  e.preventDefault();
  const ins = $$("#set-fill [data-blank]");
  const i = ins.indexOf(e.target);
  if (i >= 0 && i < ins.length - 1) ins[i + 1].focus();
  else if (!$("#set-answer").disabled) setAnswer();
});

async function setAnswer() {
  const it = st.SET.items[setAt];
  const value = it.kind === "choice" ? setPick : it.kind === "fill" ? fillValue() : $("#set-short-text").value;
  if (it.kind === "choice" && !value) return;
  if (it.kind === "fill" && !Object.values(value).some((v) => v.trim())) return;
  $("#set-answer").disabled = true;
  try {
    const body = { item: it.id, value };
    if (it.kind === "choice") body.unsure = [...setUnsure];
    const { set } = await api(`/api/sets/${st.SET.id}/answer`, { method: "POST", body });
    st.SET = set;
    resumeDeferred(set.id, it.id);
    if (it.kind === "short") setAt++;
    drawSet();
  } catch (e) { notice(esc(e.message), "error"); $("#set-answer").disabled = false; }
}
$("#set-answer").addEventListener("click", setAnswer);

async function setMove(d) {
  const it = st.SET.items[setAt];
  // 書きかけの記述は、移るときに保存しておく
  if (it && it.kind === "short" && st.SET.status === "open" && $("#set-short-text").value !== (it.answer || "")) {
    try { st.SET = (await api(`/api/sets/${st.SET.id}/answer`, { method: "POST", body: { item: it.id, value: $("#set-short-text").value } })).set; }
    catch (e) { return notice(esc(e.message), "error"); }
  }
  setAt = Math.min(Math.max(setAt + d, 0), st.SET.items.length);
  drawSet();
}
$("#set-prev").addEventListener("click", () => setMove(-1));
$("#set-next").addEventListener("click", () => setMove(1));
$("#set-back").addEventListener("click", () => { setAt = 0; drawSet(); });
$("#set-dots").addEventListener("click", (e) => {
  const b = e.target.closest("[data-si]");
  if (b) setMove(+b.dataset.si - setAt);
});

function drawFinish() {
  const s = st.SET;
  const left = s.items.filter((it) => it.answer == null).length;
  const ch = s.items.filter((it) => AUTO(it) && it.result);
  const ok = ch.filter((it) => it.result.correct).length;
  $("#set-finish-text").innerHTML = s.status === "submitted"
    ? "提出しました。Claude が記述を採点し、解説と分析のレポートを書いています。下の会話欄に進み具合が出ます。" +
      "採点が途中で止まったときは「採点をやり直す」を押してください。"
    : `択一・穴埋め ${ok} / ${ch.length}問 正解。` + (left ? `<b>まだ答えていない問題が ${left}問</b>あります（答えていない択一・穴埋めは記録しません。記述は0点になります）。` : "全部答えました。") +
      "提出すると、記述を採点し、問題ごとの解説と論点ごとの分析をレポートに保存します。";
  $("#set-submit").hidden = $("#set-back").hidden = s.status !== "open";
  $("#set-regrade").hidden = s.status !== "submitted";
}
// 提出後に採点の会話がエラーや停止で終わると、セットは「採点中」のまま残る。そこから採点だけをもう一度始める
// （採点中なら、サーバーが「いま採点中です」と断る）
$("#set-regrade").addEventListener("click", () => { if (st.SET) start({ action: "grade-set", id: st.SET.id }); });
$("#set-submit").addEventListener("click", async () => {
  const left = st.SET.items.filter((it) => it.answer == null).length;
  if (!confirm(left ? `まだ答えていない問題が ${left}問あります。提出しますか？` : "提出して、採点とレポートを作りますか？")) return;
  try {
    const r = await api(`/api/sets/${st.SET.id}/submit`, { method: "POST", body: {} });
    if (tm.id === st.SET.id) timerPause(st.SET.id);
    showSet(r.set);
    openJob(r.job);
  } catch (e) { notice(esc(e.message), "error"); }
});

function drawReport() {
  const s = st.SET;
  const wrongOnly = $("#set-wrong-only").checked;
  $("#set-compare").innerHTML = (s.compare || []).length ? `<div class="table"><table><thead><tr><th>論点</th><th>前</th><th>後</th><th>判定</th></tr></thead><tbody>${
    s.compare.map((c) => `<tr><td>${esc(c.name)}</td><td>${pct(c.before)}</td><td>${pct(c.after)}</td><td class="small">${esc(c.status_before || "")} → ${esc(c.status_after)}</td></tr>`).join("")}</tbody></table></div>` : "";
  $("#set-report-body").innerHTML = md.render(s.report || "").html;
  $("#set-review").innerHTML = s.items.map((it, i) => ({ it, i })).filter(({ it }) => !wrongOnly || !(it.result && it.result.correct)).map(({ it, i }) => {
    const r = it.result;
    const mark = !r ? "—" : it.kind === "choice" ? (r.correct ? "○" : "×") : `${r.score} / ${r.max}`;
    const ans = it.answer == null ? "（無解答）" : typeof it.answer === "object" ? Object.entries(it.answer).map(([k, v]) => `${k}＝${v || "（空欄）"}`).join("　") : it.answer;
    return `<details class="review-item"><summary><b>${i + 1}.</b> ${mark}　${esc(it.concept_names.join("、"))} <span class="muted small">${KIND_LABEL[it.kind] || "記述"}・${esc(it.slot)}</span></summary>
      <div class="md">${md.render(it.body).html}</div>
      ${it.options.length ? `<ul>${it.options.map((o) => `<li>${esc(o.label)} ${esc(o.text)}</li>`).join("")}</ul>` : ""}
      <p><b>あなたの答え：</b>${esc(ans)}</p>
      ${it.key ? `<div class="feedback md">${it.key.answer ? `<p><b>正解：${esc(it.key.answer)}</b></p>` : ""}${md.render(it.key.body).html}</div>` : ""}
    </details>`;
  }).join("") || `<p class="muted">間違えた問題はありません。</p>`;
}
$("#set-wrong-only").addEventListener("change", () => st.SET && drawReport());

// 採点が終わったら講評を表示する
export async function showResult(id, jid) {
  const f = await fetchShown("results", id, jid);
  if (!f) return;
  st.currentQ = null;
  timerSet(null);
  $("#question-card").hidden = false;
  $("#question-title").textContent = "講評 " + id;
  $("#question-meta").textContent = "";
  $("#question-body").innerHTML = md.render(f.text).html;
  $("#answer-box").hidden = true;
}
