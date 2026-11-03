// learn web UI の study.js：学習：出題の依頼、会話、択一ドリル、解答欄、タイマー、解答中の質問。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, $$, api, esc, notice, st } from "./util.js";
import { loadState, showTab } from "./main.js";
import { newSet, openSet, showResult } from "./set.js";

// ---------------------------------------------------------- 学習フォーム
function action() { return $("#start").action.value || "question"; }

export function updateRec() {
  const a = action();
  $$("#start [data-for]").forEach((el) => (el.hidden = !el.dataset.for.split(" ").includes(a)));
  let rec = "";
  if (st.S && a === "question" && st.S.recommend.written.length) rec = "おすすめ：" + st.S.recommend.written.map((r) => `${r.tag}（${r.reason}）`).join("、");
  if (st.S && a === "drill" && st.S.recommend.drill.length) rec = "おすすめ：" + st.S.recommend.drill.map((r) => `${r.tag}（${r.reason}）`).join("、");
  if (a === "review") rec = "得意・不得意な分野と、その原因、次の3問を出します。";
  $("#rec").textContent = rec;
  $("#start-btn").disabled = !!(st.S && st.S.busy);
}
$$("#start input[name=action]").forEach((r) => r.addEventListener("change", () => { updateRec(); if (action() === "past") loadPast(); }));

async function loadPast() {
  const { items } = await api("/api/items");
  const past = items.filter((x) => x.id.startsWith("past-"));
  $("#past-select").innerHTML = past.length
    ? past.map((x) => `<option value="${esc(x.id)}">${esc(x.id)}${x.graded ? "（採点済み）" : ""}</option>`).join("")
    : `<option value="">取り込んだ過去問はまだありません</option>`;
}

$("#start").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  let a = action();
  const body = { action: a === "past" ? "question" : a };
  if (a === "question" || a === "drill") body.tag = f.tag.value;
  // 択一ドリルは、問題バンクの択一だけのセットにする（その場では作らない）
  if (a === "drill") return newSet({ kind: "choice", tag: f.tag.value, n: Math.min(30, +f.count.value || st.S.drill_count) });
  if (a === "past") { body.past = f.past.value; if (!body.past) return notice("取り込んだ過去問がありません。", "warn"); }
  if (a === "import") { body.year = f.year.value.trim(); body.q = f.q.value.trim(); }
  await start(body);
});

export async function start(body) {
  try {
    const r = await api("/api/run", { method: "POST", body });
    $("#set-card").hidden = true; // 別の学習を始めたら、セットはしまう（ホームの「続きから」で戻れる）
    openJob(r.job);
    showTab("study");
    window.scrollTo({ top: 0 });
  } catch (err) { notice(esc(err.message), "error"); }
}

// ---------------------------------------------------------- 会話
export function openJob(j) {
  st.job = j;
  st.seq = 0;
  st.currentQ = null;
  timerSet(null);
  $("#log").innerHTML = "";
  $("#work").hidden = false;
  $("#question-card").hidden = true;
  $("#session-title").textContent = j.title;
  try { localStorage.setItem("learn.job", j.id); } catch (e) {}
  setStatus(j);
  poll();
}

function setStatus(j) {
  const running = j.status === "running";
  const loop = st.S && st.S.runner === "loop";
  $("#session-status").textContent = running
    ? (loop ? "Claude Code が拾うのを待っています（PC で /loop 1m /inbox を動かしておいてください）" : "Claude が作業中…（問題の作成には数分かかります）")
    : j.status === "error" ? "エラーで止まりました" : "返信できます";
  $("#session-status").classList.toggle("running", running);
  $("#stop-btn").hidden = !running || loop;
  $("#reply").hidden = running || !j.can_reply;
  $("#ask-btn").disabled = running || !j.can_reply;
  $("#start-btn").disabled = running;
  if (st.S) st.S.busy = running;
}

function addEvent(ev) {
  const log = $("#log");
  let el = null;
  if (ev.type === "user") {
    el = document.createElement("div");
    el.className = "msg user";
    el.textContent = ev.text;
  } else if (ev.type === "text") {
    el = document.createElement("div");
    el.className = "msg claude md";
    el.innerHTML = md.render(ev.text).html;
  } else if (ev.type === "tool") {
    el = document.createElement("div");
    el.className = "tool";
    el.textContent = ev.text;
  } else if (ev.type === "error") {
    el = document.createElement("div");
    el.className = "msg error";
    el.textContent = ev.text;
  } else if (ev.type === "done" && ev.seconds) {
    el = document.createElement("div");
    el.className = "tool";
    el.textContent = `— ${Math.round(ev.seconds)}秒` + (ev.cost ? `・$${ev.cost.toFixed(3)}` : "");
  }
  if (el) {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.appendChild(el);
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
}

// ポーリングは常に1本だけ。新しく始めると古いループは次の確認で抜ける
let pollGen = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function poll() { pollLoop(++pollGen); }

async function pollLoop(gen) {
  let delay = 1000;
  let lost = false;
  while (gen === pollGen && st.job) {
    const id = st.job.id;
    let r;
    try {
      r = await api(`/api/jobs/${id}?after=${st.seq}&wait=20`, { timeout: 35000 }); // サーバーは最大20秒待つ
    } catch (err) {
      if (gen !== pollGen) return;
      if (err.status === 404) {
        // 会話が消えた（サーバーの再起動や古い会話の整理）。待ち続けても戻らないので止める
        notice("この会話はもうサーバーにありません。新しくはじめてください", "error");
        try { localStorage.removeItem("learn.job"); } catch (e) {}
        setStatus({ status: "error", can_reply: false });
        st.job = null;
        return;
      }
      // サーバーが一時的に落ちても、戻ってきたら監視を続ける
      lost = true;
      notice("サーバーとの接続が切れました。再接続しています…（" + esc(err.message) + "）", "error", "conn");
      await sleep(delay);
      delay = Math.min(delay * 2, 15000);
      continue;
    }
    if (lost) { if (st.noticeOwner === "conn") notice(""); lost = false; delay = 1000; }
    if (gen !== pollGen || !st.job || st.job.id !== id) return;
    r.events.forEach(addEvent);
    st.seq += r.events.length;
    st.job = r.job;
    setStatus(st.job);
    const ended = r.events.some((e) => e.type === "end");
    const q = st.job.question_ids[st.job.question_ids.length - 1];
    if (q && (q !== st.currentQ || ended)) await showQuestion(q, q !== st.currentQ, id);
    if (st.job.status !== "running") {
      if (ended) {
        loadState().catch((e) => notice("成績の表示を更新できませんでした（" + esc(e.message) + "）", "error"));
        const t = st.job.target || (q && q.startsWith("drill-") ? q : null);
        if (t && t.startsWith("set-")) await openSet(t, true);
        else if (t) await showResult(t, id);
      }
      return;
    }
  }
}

// 問題・講評の表示は、あとから始めた取得を優先する（古い取得の結果で新しい表示を上書きしない）
let showGen = 0;
export async function fetchShown(kind, id, jid) {
  const gen = ++showGen;
  let f;
  try {
    f = await api(`/api/file?kind=${kind}&id=${encodeURIComponent(id)}`);
  } catch (e) {
    if (gen === showGen && st.job && st.job.id === jid) {
      notice(`${kind === "results" ? "講評" : "問題"}を読み込めませんでした。履歴から開いてください（${esc(e.message)}）`, "error");
    }
    return null;
  }
  if (gen !== showGen || !st.job || st.job.id !== jid) return null; // 取得中に別の会話・別の表示に切り替わった
  return f;
}

async function showQuestion(id, fresh, jid) {
  if (st.job && st.job.status === "running" && !fresh) return;
  const q = await fetchShown("questions", id, jid);
  if (!q) return;
  const r = md.render(q.text);
  if (r.meta.verified === "false") return; // 検証が終わるまでは出さない
  st.currentQ = id;
  $("#question-card").hidden = false;
  $("#question-title").textContent = id;
  $("#question-meta").textContent = [r.meta.tags && "分野 " + r.meta.tags, r.meta.time_limit_min && "目安 " + r.meta.time_limit_min + "分"].filter(Boolean).join("・");
  $("#question-body").innerHTML = r.html;
  const isDrill = id.startsWith("drill-");
  $("#answer-box").hidden = isDrill;
  if (isDrill) renderDrill(id, q.text);
  if (isDrill) timerSet(null);
  else timerSet(id, parseFloat(r.meta.time_limit_min) || null);
  if (!isDrill && fresh) {
    // 読み込みが終わるまでは保存させない（空の解答で上書きしないように）
    const load = (answerLoad = { id, jid, state: "loading" });
    ans = { fs: answer.fields(q.text), head: answer.header(id, nowIso()), raw: false };
    fillAnswer("");
    try {
      const a = await api(`/api/file?kind=answers&id=${encodeURIComponent(id)}`);
      if (answerLoad !== load || !st.job || st.job.id !== jid) return;
      fillAnswer(a.text);
      $("#answer-status").textContent = "";
      load.state = "ok";
    } catch (e) {
      if (answerLoad !== load || !st.job || st.job.id !== jid) return;
      if (e.status === 404) { load.state = "ok"; return; } // まだ書いていない
      load.state = "failed";
      notice("解答を読み込めませんでした。再読み込みしてください（" + esc(e.message) + "）", "error");
    }
  }
}

// ---------------------------------------------------------- 択一ドリル（/drill）をクリックで1問ずつ
// 答えはまとめて「1ア 2ウ …」の形で会話に返す（答え合わせは /drill スキル）
const CHOICE_RE = /^\s*[-*]\s*([ア-ンA-Z])[\s　.．:：)）]\s*(.+)$/;
let drill = { id: null, qs: [], at: 0, picks: {}, sent: false };
function parseDrill(text) {
  const body = text.replace(/^---\n[\s\S]*?\n---\n?/, "");
  const parts = body.split(/^(?=\*\*問\s*\d+\*\*)/m);
  const intro = /^\*\*問/.test(parts[0]) ? "" : parts.shift();
  const qs = parts.map((p) => {
    const n = +p.match(/^\*\*問\s*(\d+)/)[1];
    const options = [], rest = [];
    p.split("\n").forEach((l) => { const m = l.match(CHOICE_RE); if (m) options.push({ label: m[1], text: m[2] }); else rest.push(l); });
    return { n, body: rest.join("\n").trim(), options };
  }).filter((x) => x.options.length);
  return { intro, qs };
}
function renderDrill(id, text) {
  const { intro, qs } = parseDrill(text);
  if (!qs.length) return; // 読み取れない形なら、ふつうに表示して返信欄で答える
  if (drill.id !== id) drill = { id, qs, at: 0, picks: {}, sent: false, intro };
  else Object.assign(drill, { qs, intro });
  drawDrill();
}
function drawDrill() {
  const d = drill, q = d.qs[d.at];
  const done = Object.keys(d.picks).length;
  $("#question-body").innerHTML = `${md.render(d.intro || "").html}
    <div class="set-dots">${d.qs.map((x, i) => `<button type="button" data-dq="${i}" class="${i === d.at ? "cur " : ""}${d.picks[x.n] ? "done" : ""}">${x.n}</button>`).join("")}</div>
    <div class="md">${md.render(q.body).html}</div>
    <div class="options">${q.options.map((o) => `<button type="button" data-dopt="${esc(o.label)}" class="${d.picks[q.n] === o.label ? "sel" : ""}"${d.sent ? " disabled" : ""}><span class="lbl">${esc(o.label)}</span><span>${md.render(o.text).html.replace(/^<p>|<\/p>$/g, "")}</span></button>`).join("")}</div>
    <div class="row">
      <button type="button" data-dmove="-1"${d.at === 0 ? " disabled" : ""}>前へ</button>
      <button type="button" data-dmove="1"${d.at === d.qs.length - 1 ? " disabled" : ""}>次へ</button>
      <button type="button" class="primary" id="drill-send"${d.sent || !done ? " disabled" : ""}>答えを送る（${done} / ${d.qs.length}）</button>
      <span class="muted small">${d.sent ? "送りました。答え合わせを待っています" : "わからない問題は選ばずに送ると「?」になります"}</span>
    </div>`;
}
$("#question-body").addEventListener("click", async (e) => {
  if (!drill.id || st.currentQ !== drill.id) return;
  const d = drill, q = d.qs[d.at];
  const o = e.target.closest("[data-dopt]"), mv = e.target.closest("[data-dmove]"), dq = e.target.closest("[data-dq]");
  if (o && !d.sent) {
    d.picks[q.n] = o.dataset.dopt;
    if (d.at < d.qs.length - 1) setTimeout(() => { d.at++; drawDrill(); }, 250); // 選んだら次の問題へ
    drawDrill();
  } else if (mv) { d.at = Math.min(Math.max(d.at + +mv.dataset.dmove, 0), d.qs.length - 1); drawDrill(); }
  else if (dq) { d.at = +dq.dataset.dq; drawDrill(); }
  else if (e.target.id === "drill-send") {
    const left = d.qs.filter((x) => !d.picks[x.n]).length;
    if (left && !confirm(`まだ選んでいない問題が ${left}問あります（「?」として送ります）。送りますか？`)) return;
    const text = d.qs.map((x) => `${x.n}${d.picks[x.n] || "?"}`).join(" ");
    try {
      const r = await api(`/api/jobs/${st.job.id}/reply`, { method: "POST", body: { text } });
      d.sent = true;
      st.job = r.job;
      setStatus(st.job);
      drawDrill();
      poll();
    } catch (err) { notice(esc(err.message), "error"); }
  }
});

// ---------------------------------------------------------- 解答欄（設問ごと）
// ans.fs：問題文から読んだ欄の一覧、ans.head：解答ファイルの front matter と「# 解答」、
// ans.raw：欄に分けずに1つのテキストで編集しているか（欄を読み取れない問題・解答のとき）
let ans = { fs: [], head: "", raw: true };

function nowIso() {
  const d = new Date();
  const p = (n) => String(Math.abs(n)).padStart(2, "0");
  const off = -d.getTimezoneOffset();
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}` +
    `${off >= 0 ? "+" : "-"}${p(Math.trunc(off / 60))}:${p(off % 60)}`;
}

function fillAnswer(text) {
  const p = text ? answer.parse(text, ans.fs) : { header: "", values: {} };
  if (p && p.header.trim()) ans.head = p.header;
  ans.raw = !ans.fs.length || !p;
  // 「## 設問」のない問題は1つの欄で書く。新しく書くときは front matter（id・started_at）を入れておく
  $("#answer-text").value = text || (ans.fs.length ? "" : ans.head + "\n\n");
  renderFields(p ? p.values : {});
  $("#ask-target").innerHTML = `<option value="">問題全体・本文</option>` +
    ans.fs.map((f) => `<option>${esc(f.label)}</option>`).join("");
}

function renderFields(values) {
  $("#answer-fields").innerHTML = ans.fs.map((f) => `<div class="ans-field">
      <div class="ans-head"><b>${esc(f.label)}</b><span class="ans-prompt">${esc(f.prompt)}</span></div>
      <textarea data-key="${esc(f.key)}" rows="${f.limit ? Math.min(6, Math.ceil(f.limit / 40) + 1) : 3}" aria-label="${esc(f.label)}"></textarea>
      <div class="ans-count"></div></div>`).join("");
  $$("#answer-fields textarea").forEach((t) => { t.value = values[t.dataset.key] || ""; });
  showMode();
}

function fieldValues() {
  const v = {};
  $$("#answer-fields textarea").forEach((t) => { v[t.dataset.key] = t.value; });
  return v;
}

function answerText() {
  return ans.raw ? $("#answer-text").value : answer.build(ans.head, ans.fs, fieldValues());
}

function showMode() {
  $("#answer-fields").hidden = ans.raw;
  $("#answer-text").hidden = !ans.raw;
  $("#answer-mode").hidden = !ans.fs.length;
  $("#answer-mode").textContent = ans.raw ? "設問ごとの欄に戻す" : "まとめて編集";
  updateCounts();
}

// 字数：改行は数えない。制限の9割を超えたら色を変え、超えたら赤くする（本番では超過は0点）
function updateCounts() {
  if (ans.raw) { $("#answer-total").textContent = ""; return; }
  let over = 0, filled = 0;
  $$("#answer-fields .ans-field").forEach((el, i) => {
    const f = ans.fs[i];
    const n = answer.count(el.querySelector("textarea").value);
    const c = el.querySelector(".ans-count");
    if (n) filled++;
    if (f.limit) {
      c.textContent = n > f.limit ? `${n} / ${f.limit}字（${n - f.limit}字オーバー）` : `${n} / ${f.limit}字`;
      c.className = "ans-count" + (n > f.limit ? " over" : n >= f.limit * 0.9 ? " near" : "");
      if (n > f.limit) over++;
    } else {
      c.textContent = `${n}字`;
      c.className = "ans-count";
    }
  });
  $("#answer-total").textContent = `${filled} / ${ans.fs.length} 問記入` + (over ? `・字数オーバー ${over}問` : "");
}
$("#answer-fields").addEventListener("input", () => { updateCounts(); timerAutoStart(); });
$("#answer-text").addEventListener("input", timerAutoStart);

$("#answer-mode").addEventListener("click", () => {
  if (ans.raw) {
    const p = answer.parse($("#answer-text").value, ans.fs);
    if (!p) return notice("欄に分けられない書き込みがあります。「## 設問1」「(1)」の見出しの下に書いてください", "warn");
    if (p.header.trim()) ans.head = p.header;
    ans.raw = false;
    renderFields(p.values);
  } else {
    $("#answer-text").value = answerText();
    ans.raw = true;
    showMode();
  }
});

let answerLoad = null;
async function saveAnswer(submit) {
  if (!st.currentQ) return;
  if (!answerLoad || answerLoad.id !== st.currentQ || answerLoad.state !== "ok") {
    throw new Error(answerLoad && answerLoad.state === "loading" ? "解答を読み込み中です" : "解答を読み込めていないので保存できません");
  }
  let text = answerText();
  if (submit) {
    text = answer.setMeta(text, "submitted_at", nowIso());
    const ms = timerElapsed(st.currentQ);
    // タイマーを使わなかった（0に戻した）ときは前回の値を残さない。採点は started_at との差を使う
    text = ms > 0 ? answer.setMeta(text, "elapsed_min", Math.max(1, Math.round(ms / 60000))) : answer.delMeta(text, "elapsed_min");
  }
  await api(`/api/answers/${encodeURIComponent(st.currentQ)}`, { method: "PUT", body: { text } });
  $("#answer-status").textContent = "保存しました " + new Date().toLocaleTimeString();
}

// ---------------------------------------------------------- タイマー
// 問題ごとに localStorage へ {acc: 止めるまでに貯めたミリ秒, since: 動かし始めた時刻} を持つ。
// 同じブラウザなら再読み込みしても続きから測れる（別の端末とは共有しない）。別の問題や講評に移ると一時停止する。目安時間（time_limit_min）を過ぎたら赤くする
export const tm = { id: null, limit: null, warned: false, iv: null };
const tKey = (id) => "learn.timer." + id;
function tLoad(id) {
  try { return JSON.parse(localStorage.getItem(tKey(id))) || { acc: 0, since: null }; } catch (e) { return { acc: 0, since: null }; }
}
function tSave(id, t) { try { localStorage.setItem(tKey(id), JSON.stringify(t)); } catch (e) {} }
function timerElapsed(id) { const t = tLoad(id); return t.acc + (t.since ? Date.now() - t.since : 0); }
function fmtMs(ms) {
  const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function timerSet(id, limitMin) {
  if (tm.id && tm.id !== id) timerPause(tm.id); // 離れていた時間は数えない
  if (tm.id !== id) tm.warned = false;
  tm.id = id;
  tm.limit = limitMin ? limitMin * 60000 : null;
  clearInterval(tm.iv);
  tm.iv = id ? setInterval(timerRender, 1000) : null;
  timerRender();
}
function timerStart(id) { const t = tLoad(id); if (!t.since) { t.since = Date.now(); tSave(id, t); } }
export function timerPause(id) { const t = tLoad(id); if (t.since) { t.acc += Date.now() - t.since; t.since = null; tSave(id, t); } timerRender(); }
export function timerAutoStart() {
  if (!tm.id) return;
  const t = tLoad(tm.id);
  if (!t.since && !t.acc) { timerStart(tm.id); timerRender(); }
}

export function timerRender() {
  const el = $("#timer");
  const visible = !!tm.id && !$("#tab-study").hidden && (!$("#question-card").hidden || (!!st.SET && tm.id === st.SET.id && !$("#set-card").hidden));
  el.hidden = !visible;
  if (!visible) return;
  const t = tLoad(tm.id);
  const ms = timerElapsed(tm.id);
  const over = tm.limit && ms > tm.limit;
  $("#timer-text").textContent = tm.limit
    ? (over ? `${fmtMs(ms)}（${fmtMs(ms - tm.limit)} 超過）` : `${fmtMs(ms)} / ${fmtMs(tm.limit)}`)
    : fmtMs(ms);
  el.classList.toggle("over", !!over);
  el.classList.toggle("running", !!t.since);
  $("#timer-toggle").textContent = t.since ? "一時停止" : t.acc ? "再開" : "開始";
  if (over && t.since && !tm.warned) {
    tm.warned = true;
    notice(`目安時間（${Math.round(tm.limit / 60000)}分）を過ぎました。本番ならここで見直しに移る時間です。`, "warn");
  }
}
$("#timer-toggle").addEventListener("click", () => {
  if (!tm.id) return;
  if (tLoad(tm.id).since) timerPause(tm.id);
  else { timerStart(tm.id); timerRender(); }
});
$("#timer-reset").addEventListener("click", () => {
  if (!tm.id || !confirm("タイマーを 0 に戻しますか？")) return;
  tSave(tm.id, { acc: 0, since: null });
  tm.warned = false;
  timerRender();
});

// ---------------------------------------------------------- 解答中の質問
// 問題を出した会話の続きとして送る。返し方（答えを匂わせない）は /inbox スキルと CLAUDE.md に書いてある
const ASK_KINDS = {
  word: ["言葉の意味", "例：DMARC、p=none（わからない言葉を書く）"],
  ask: ["何を聞かれているか", "例：「どう扱われるか」は何について書けばいい？"],
  worry: ["不安・その他", "例：この設問は2つ書く必要がある？"],
};
function askPlaceholder() { $("#ask-text").placeholder = ASK_KINDS[$("#ask").kind.value][1]; }
$$("#ask input[name=kind]").forEach((r) => r.addEventListener("change", askPlaceholder));
askPlaceholder();

$("#ask").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const text = $("#ask-text").value.trim();
  if (!text || !st.job || !st.currentQ) return;
  if (st.job.status === "running") return notice("前の返事を待っています。届いてからもう一度どうぞ", "warn");
  try {
    const r = await api("/api/ask", { method: "POST", body: { job: st.job.id, id: st.currentQ, part: f.part.value, kind: f.kind.value, text } });
    $("#ask-text").value = "";
    st.job = r.job;
    setStatus(st.job);
    poll();
  } catch (err) { notice(esc(err.message), "error"); }
});
$("#save-answer").addEventListener("click", () => saveAnswer().catch((e) => notice(esc(e.message), "error")));
$("#grade-btn").addEventListener("click", async () => {
  try {
    const qid = st.currentQ;
    await saveAnswer();
    if (!confirm("提出して採点しますか？") || st.currentQ !== qid) return;
    timerPause(qid);
    await saveAnswer(true);
    if (st.currentQ !== qid) return notice("提出の間に別の問題に切り替わったので、採点は始めませんでした", "warn");
    await start({ action: "grade", id: qid });
  } catch (e) { notice(esc(e.message), "error"); }
});

$("#reply").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#reply-text").value.trim();
  if (!text || !st.job) return;
  try {
    const r = await api(`/api/jobs/${st.job.id}/reply`, { method: "POST", body: { text } });
    $("#reply-text").value = "";
    st.job = r.job;
    setStatus(st.job);
    poll();
  } catch (err) { notice(esc(err.message), "error"); }
});
$("#reply-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#reply").requestSubmit();
});
$("#stop-btn").addEventListener("click", () => st.job && api(`/api/jobs/${st.job.id}/stop`, { method: "POST" }));
