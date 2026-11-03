// learn web UI。サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript。
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = md.esc;
  let S = null; // /api/state
  let job = null; // いま表示している会話
  let seq = 0;
  let currentQ = null;

  async function api(path, opts = {}) {
    const o = { headers: { "X-Learn": "1" }, ...opts };
    if (o.body && typeof o.body !== "string") {
      o.body = JSON.stringify(o.body);
      o.headers["Content-Type"] = "application/json";
    }
    const r = await fetch(path, o);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const err = new Error(data.error || r.statusText);
      err.status = r.status;
      throw err;
    }
    return data;
  }

  function notice(msg, kind) {
    const n = $("#notice");
    if (!msg) { n.hidden = true; return; }
    n.hidden = false;
    n.className = "notice " + (kind || "");
    n.innerHTML = msg;
  }

  // ---------------------------------------------------------- タブ
  function showTab(name) {
    $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
    try { localStorage.setItem("learn.tab", name); } catch (e) {}
    if (name === "stats") loadStats();
    if (name === "history") loadItems();
    if (name === "settings") loadProfile();
  }
  $$("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

  // ---------------------------------------------------------- 状態
  async function loadState() {
    S = await api("/api/state");
    $("#exam").textContent = S.exam || "";
    if (S.profile !== "ok") {
      notice(S.profile === "missing"
        ? "まだ試験が設定されていません。<a href=\"#\" data-go=\"settings\">設定</a>から選んでください。"
        : "<code>exam/profile.md</code> に TODO が残っています。<a href=\"#\" data-go=\"settings\">設定</a>で埋めてください。", "warn");
    } else if (S.runner === "cli" && !S.claude) {
      notice("Claude Code（<code>claude</code> コマンド）が見つかりません。出題・採点には Claude Code のインストールとログインが必要です。", "warn");
    } else notice("");

    const sel = $("#tag-select");
    const keep = sel.value;
    const recW = S.recommend.written.map((r) => r.tag);
    sel.innerHTML = `<option value="">おまかせ（弱点から自動）</option>` +
      S.tags.map((t) => `<option value="${esc(t.tag)}">${esc(t.tag)}：${esc(t.name)}</option>`).join("");
    sel.value = keep;
    $("#drill-count").placeholder = S.drill_count;
    $("#example-select").innerHTML = S.examples.map((e) => `<option value="${esc(e.key)}">${esc(e.name)}</option>`).join("");
    updateRec();
    return recW;
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-go]");
    if (a) { e.preventDefault(); showTab(a.dataset.go); }
  });

  // ---------------------------------------------------------- 学習フォーム
  function action() { return $("#start").action.value || "question"; }

  function updateRec() {
    const a = action();
    $$("#start [data-for]").forEach((el) => (el.hidden = !el.dataset.for.split(" ").includes(a)));
    let rec = "";
    if (S && a === "question" && S.recommend.written.length) rec = "おすすめ：" + S.recommend.written.map((r) => `${r.tag}（${r.reason}）`).join("、");
    if (S && a === "drill" && S.recommend.drill.length) rec = "おすすめ：" + S.recommend.drill.map((r) => `${r.tag}（${r.reason}）`).join("、");
    if (a === "review") rec = "得意・不得意な分野と、その原因、次の3問を出します。";
    $("#rec").textContent = rec;
    $("#start-btn").disabled = !!(S && S.busy);
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
    if (a === "drill") body.count = f.count.value;
    if (a === "past") { body.past = f.past.value; if (!body.past) return notice("取り込んだ過去問がありません。", "warn"); }
    if (a === "import") { body.year = f.year.value.trim(); body.q = f.q.value.trim(); }
    await start(body);
  });

  async function start(body) {
    try {
      const r = await api("/api/run", { method: "POST", body });
      openJob(r.job);
      showTab("study");
    } catch (err) { notice(esc(err.message), "error"); }
  }

  // ---------------------------------------------------------- 会話
  function openJob(j) {
    job = j;
    seq = 0;
    currentQ = null;
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
    const loop = S && S.runner === "loop";
    $("#session-status").textContent = running
      ? (loop ? "Claude Code が拾うのを待っています（PC で /loop 1m /inbox を動かしておいてください）" : "Claude が作業中…（問題の作成には数分かかります）")
      : j.status === "error" ? "エラーで止まりました" : "返信できます";
    $("#session-status").classList.toggle("running", running);
    $("#stop-btn").hidden = !running || loop;
    $("#reply").hidden = running || !j.can_reply;
    $("#start-btn").disabled = running;
    if (S) S.busy = running;
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
    while (gen === pollGen && job) {
      const id = job.id;
      let r;
      try {
        r = await api(`/api/jobs/${id}?after=${seq}&wait=20`);
      } catch (err) {
        if (gen !== pollGen) return;
        if (err.status === 404) {
          // 会話が消えた（サーバーの再起動や古い会話の整理）。待ち続けても戻らないので止める
          notice("この会話はもうサーバーにありません。新しくはじめてください", "error");
          try { localStorage.removeItem("learn.job"); } catch (e) {}
          return;
        }
        // サーバーが一時的に落ちても、戻ってきたら監視を続ける
        lost = true;
        notice("サーバーとの接続が切れました。再接続しています…（" + esc(err.message) + "）", "error");
        await sleep(delay);
        delay = Math.min(delay * 2, 15000);
        continue;
      }
      if (lost) { notice(""); lost = false; delay = 1000; }
      if (gen !== pollGen || !job || job.id !== id) return;
      r.events.forEach(addEvent);
      seq += r.events.length;
      job = r.job;
      setStatus(job);
      const ended = r.events.some((e) => e.type === "end");
      const q = job.question_ids[job.question_ids.length - 1];
      if (q && (q !== currentQ || ended)) await showQuestion(q, q !== currentQ, id);
      if (job.status !== "running") {
        if (ended) {
          loadState();
          const t = job.target || (q && q.startsWith("drill-") ? q : null);
          if (t) await showResult(t, id);
        }
        return;
      }
    }
  }

  async function showQuestion(id, fresh, jid) {
    if (job && job.status === "running" && !fresh) return;
    let q;
    try { q = await api(`/api/file?kind=questions&id=${encodeURIComponent(id)}`); } catch (e) { return; }
    if (!job || job.id !== jid) return; // 取得中に別の会話に切り替わった
    const r = md.render(q.text);
    if (r.meta.verified === "false") return; // 検証が終わるまでは出さない
    currentQ = id;
    $("#question-card").hidden = false;
    $("#question-title").textContent = id;
    $("#question-meta").textContent = [r.meta.tags && "分野 " + r.meta.tags, r.meta.time_limit_min && "目安 " + r.meta.time_limit_min + "分"].filter(Boolean).join("・");
    $("#question-body").innerHTML = r.html;
    const isDrill = id.startsWith("drill-");
    $("#answer-box").hidden = isDrill;
    if (!isDrill && fresh) {
      try {
        const a = await api(`/api/file?kind=answers&id=${encodeURIComponent(id)}`);
        if (!job || job.id !== jid) return;
        $("#answer-text").value = a.text;
        $("#answer-status").textContent = "";
      } catch (e) {
        if (job && job.id === jid) $("#answer-text").value = "";
      }
    }
  }

  // 採点が終わったら講評を表示する
  async function showResult(id, jid) {
    let f;
    try { f = await api(`/api/file?kind=results&id=${encodeURIComponent(id)}`); } catch (e) { return; }
    if (!job || job.id !== jid) return; // 取得中に別の会話に切り替わった
    currentQ = null;
    $("#question-card").hidden = false;
    $("#question-title").textContent = "講評 " + id;
    $("#question-meta").textContent = "";
    $("#question-body").innerHTML = md.render(f.text).html;
    $("#answer-box").hidden = true;
  }

  async function saveAnswer() {
    if (!currentQ) return;
    await api(`/api/answers/${encodeURIComponent(currentQ)}`, { method: "PUT", body: { text: $("#answer-text").value } });
    $("#answer-status").textContent = "保存しました " + new Date().toLocaleTimeString();
  }
  $("#save-answer").addEventListener("click", () => saveAnswer().catch((e) => notice(esc(e.message), "error")));
  $("#grade-btn").addEventListener("click", async () => {
    try {
      await saveAnswer();
      if (!confirm("提出して採点しますか？")) return;
      await start({ action: "grade", id: currentQ });
    } catch (e) { notice(esc(e.message), "error"); }
  });

  $("#reply").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#reply-text").value.trim();
    if (!text || !job) return;
    try {
      const r = await api(`/api/jobs/${job.id}/reply`, { method: "POST", body: { text } });
      $("#reply-text").value = "";
      job = r.job;
      setStatus(job);
      poll();
    } catch (err) { notice(esc(err.message), "error"); }
  });
  $("#reply-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#reply").requestSubmit();
  });
  $("#stop-btn").addEventListener("click", () => job && api(`/api/jobs/${job.id}/stop`, { method: "POST" }));

  // ---------------------------------------------------------- 成績
  const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
  const MISS = { knowledge: "知識", reading: "読解", writing: "記述" };

  async function loadStats() {
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
        <p class="muted small">詳しい原因の読み解きと次の一手は「学習する」の「振り返り」で Claude が出します。</p>`;
    } catch (e) { box.textContent = e.message; }
  }

  // ---------------------------------------------------------- 履歴
  async function loadItems() {
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
      $("#viewer-body").innerHTML = md.render(f.text).html;
    } catch (e) { $("#viewer-body").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
    $("#viewer").scrollIntoView({ behavior: "smooth" });
  }

  // ---------------------------------------------------------- 設定
  let profileDirty = false;
  $("#profile-text").addEventListener("input", () => { profileDirty = true; });
  async function loadProfile(force) {
    if (profileDirty && !force) return; // 書きかけを消さない
    const { text } = await api("/api/profile");
    if (profileDirty && !force) return;
    profileDirty = false;
    $("#profile-text").value = text || "";
    $("#profile-text").placeholder = "まだありません。上でサンプルを選ぶか、対話で設定してください。";
  }
  $("#copy-example").addEventListener("click", async () => {
    const key = $("#example-select").value;
    if (!key) return;
    try {
      await api("/api/setup-example", { method: "POST", body: { key } });
    } catch (e) {
      if (!/すでに/.test(e.message)) return notice(esc(e.message), "error");
      if (!confirm("いまの exam/profile.md を上書きしますか？（学習の記録は消えません）")) return;
      await api("/api/setup-example", { method: "POST", body: { key, overwrite: true } });
    }
    await loadProfile(true);
    await loadState();
    $("#profile-status").textContent = "コピーしました。目標受験時期と学習者メモを書き換えて保存してください。";
  });
  $("#setup-chat").addEventListener("click", () => start({ action: "setup" }));
  $("#save-profile").addEventListener("click", async () => {
    try {
      await api("/api/profile", { method: "PUT", body: { text: $("#profile-text").value } });
      profileDirty = false;
      $("#profile-status").textContent = "保存しました";
      loadState();
    } catch (e) { notice(esc(e.message), "error"); }
  });

  // ---------------------------------------------------------- 起動
  (async function init() {
    try { await loadState(); } catch (e) { notice(esc(e.message), "error"); return; }
    let tab = "study";
    try { tab = localStorage.getItem("learn.tab") || tab; } catch (e) {}
    if (S.profile !== "ok") tab = "settings";
    showTab(tab);
    // 再読み込みしても、最後の会話に戻れるように
    try {
      const { jobs } = await api("/api/jobs");
      let last = null;
      try { last = localStorage.getItem("learn.job"); } catch (e) {}
      // 実行中の会話を優先する（別の端末で始めたものも含めて）
      const j = jobs.find((x) => x.status === "running") || jobs.find((x) => x.id === last);
      if (j) openJob(j);
    } catch (e) {}
  })();
})();
