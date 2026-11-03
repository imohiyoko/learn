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
    const { timeout, ...rest } = opts;
    const o = { headers: { "X-Learn": "1" }, ...rest };
    if (o.body && typeof o.body !== "string") {
      o.body = JSON.stringify(o.body);
      o.headers["Content-Type"] = "application/json";
    }
    // timeout（ミリ秒）を過ぎたら打ち切る。応答も切断もしない回線で待ち続けないように
    const ctl = timeout ? new AbortController() : null;
    const timer = ctl && setTimeout(() => ctl.abort(), timeout);
    if (ctl) o.signal = ctl.signal;
    let r, data;
    try {
      r = await fetch(path, o);
      data = await r.json().catch(() => ({}));
    } catch (e) {
      if (e.name === "AbortError") throw new Error("応答がありません");
      throw e;
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (!r.ok) {
      const err = new Error(data.error || r.statusText);
      err.status = r.status;
      throw err;
    }
    return data;
  }

  // owner は通知を出した側（接続断の通知を、再接続したときにだけ消すため）
  let noticeOwner = null;
  function notice(msg, kind, owner) {
    const n = $("#notice");
    noticeOwner = msg ? owner || null : null;
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
  async function loadState() {
    S = await api("/api/state");
    $("#exam").textContent = S.exam || "";
    if (S.profile !== "ok") {
      notice(S.profile === "missing"
        ? "まだ資格が選ばれていません。<a href=\"#\" data-go=\"home\">ホーム</a>の「資格を選ぶ」から選んでください。"
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
    // 択一ドリルは、問題バンクの択一だけのセットにする（その場では作らない）
    if (a === "drill") return newSet({ kind: "choice", tag: f.tag.value, n: Math.min(30, +f.count.value || S.drill_count) });
    if (a === "past") { body.past = f.past.value; if (!body.past) return notice("取り込んだ過去問がありません。", "warn"); }
    if (a === "import") { body.year = f.year.value.trim(); body.q = f.q.value.trim(); }
    await start(body);
  });

  async function start(body) {
    try {
      const r = await api("/api/run", { method: "POST", body });
      $("#set-card").hidden = true; // 別の学習を始めたら、セットはしまう（ホームの「続きから」で戻れる）
      openJob(r.job);
      showTab("study");
      window.scrollTo({ top: 0 });
    } catch (err) { notice(esc(err.message), "error"); }
  }

  // ---------------------------------------------------------- ホーム
  const STATUS_ORDER = { "苦手": 0, "ボーダー": 1, "測定中": 2, "未出題": 3, "得意": 4 };

  let homeGen = 0;
  async function loadHome() {
    if (!S) return;
    const gen = ++homeGen; // 資格を切り替えたあとに古い応答が届いたら捨てる
    const ok = S.profile === "ok";
    $("#home-exam-name").textContent = ok ? S.exam || "いまの資格" : "まだ資格が選ばれていません";
    $("#home-actions").hidden = !ok;
    $("#home-tags-card").hidden = !ok;
    $("#home-rec").textContent = ok && S.recommend.written.length
      ? "おすすめの分野：" + S.recommend.written.map((r) => `${r.name}（${r.reason}）`).join("、") : "";
    renderExamples();
    api("/api/glossary").then((g) => {
      const todo = g.terms.filter((x) => !x.known).length;
      $("#home-glossary").hidden = !todo;
      $("#home-glossary").innerHTML = `単語帳：まだ覚えていない言葉が ${todo}語あります。<a href="#" data-go="glossary">単語帳を開く</a>`;
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

    const tags = S.tags.map((t) => ({ ...t, a: (a && a.tags[t.tag]) || {} }))
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
    $("#home-examples").innerHTML = S.examples.map((e) => {
      const cur = S.profile === "ok" && S.exam === e.name;
      return `<div class="exam-card${cur ? " current" : ""}"><b>${esc(e.name)}</b>
        ${cur ? `<span class="badge">勉強中</span>` : `<button class="small" data-example="${esc(e.key)}">この資格で勉強する</button>`}</div>`;
    }).join("") || `<p class="muted small">用意してある資格はまだありません。</p>`;
  }

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-start], [data-example], [data-job], [data-set]");
    if (!b) return;
    e.preventDefault();
    if (b.dataset.set) return openSet(b.dataset.set);
    if (b.dataset.start === "drill") return newSet({ kind: "choice", tag: b.dataset.tag || "", n: Math.min(30, S ? S.drill_count : 10) });
    if (b.dataset.start) return start({ action: b.dataset.start, tag: b.dataset.tag || "" });
    if (b.dataset.example) return useExample(b.dataset.example);
    if (b.dataset.job) {
      try {
        const { jobs } = await api("/api/jobs");
        const j = jobs.find((x) => x.id === b.dataset.job);
        if (!j) return notice("この会話はもうありません", "warn");
        $("#set-card").hidden = true;
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

  async function loadRefs() {
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

  // ---------------------------------------------------------- 会話
  function openJob(j) {
    job = j;
    seq = 0;
    currentQ = null;
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
    const loop = S && S.runner === "loop";
    $("#session-status").textContent = running
      ? (loop ? "Claude Code が拾うのを待っています（PC で /loop 1m /inbox を動かしておいてください）" : "Claude が作業中…（問題の作成には数分かかります）")
      : j.status === "error" ? "エラーで止まりました" : "返信できます";
    $("#session-status").classList.toggle("running", running);
    $("#stop-btn").hidden = !running || loop;
    $("#reply").hidden = running || !j.can_reply;
    $("#ask-btn").disabled = running || !j.can_reply;
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
        r = await api(`/api/jobs/${id}?after=${seq}&wait=20`, { timeout: 35000 }); // サーバーは最大20秒待つ
      } catch (err) {
        if (gen !== pollGen) return;
        if (err.status === 404) {
          // 会話が消えた（サーバーの再起動や古い会話の整理）。待ち続けても戻らないので止める
          notice("この会話はもうサーバーにありません。新しくはじめてください", "error");
          try { localStorage.removeItem("learn.job"); } catch (e) {}
          setStatus({ status: "error", can_reply: false });
          job = null;
          return;
        }
        // サーバーが一時的に落ちても、戻ってきたら監視を続ける
        lost = true;
        notice("サーバーとの接続が切れました。再接続しています…（" + esc(err.message) + "）", "error", "conn");
        await sleep(delay);
        delay = Math.min(delay * 2, 15000);
        continue;
      }
      if (lost) { if (noticeOwner === "conn") notice(""); lost = false; delay = 1000; }
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
          loadState().catch((e) => notice("成績の表示を更新できませんでした（" + esc(e.message) + "）", "error"));
          const t = job.target || (q && q.startsWith("drill-") ? q : null);
          if (t && t.startsWith("set-")) await openSet(t, true);
          else if (t) await showResult(t, id);
        }
        return;
      }
    }
  }

  // 問題・講評の表示は、あとから始めた取得を優先する（古い取得の結果で新しい表示を上書きしない）
  let showGen = 0;
  async function fetchShown(kind, id, jid) {
    const gen = ++showGen;
    let f;
    try {
      f = await api(`/api/file?kind=${kind}&id=${encodeURIComponent(id)}`);
    } catch (e) {
      if (gen === showGen && job && job.id === jid) {
        notice(`${kind === "results" ? "講評" : "問題"}を読み込めませんでした。履歴から開いてください（${esc(e.message)}）`, "error");
      }
      return null;
    }
    if (gen !== showGen || !job || job.id !== jid) return null; // 取得中に別の会話・別の表示に切り替わった
    return f;
  }

  async function showQuestion(id, fresh, jid) {
    if (job && job.status === "running" && !fresh) return;
    const q = await fetchShown("questions", id, jid);
    if (!q) return;
    const r = md.render(q.text);
    if (r.meta.verified === "false") return; // 検証が終わるまでは出さない
    currentQ = id;
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
        if (answerLoad !== load || !job || job.id !== jid) return;
        fillAnswer(a.text);
        $("#answer-status").textContent = "";
        load.state = "ok";
      } catch (e) {
        if (answerLoad !== load || !job || job.id !== jid) return;
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
    if (!drill.id || currentQ !== drill.id) return;
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
        const r = await api(`/api/jobs/${job.id}/reply`, { method: "POST", body: { text } });
        d.sent = true;
        job = r.job;
        setStatus(job);
        drawDrill();
        poll();
      } catch (err) { notice(esc(err.message), "error"); }
    }
  });

  // ---------------------------------------------------------- セット（問題バンク）
  // scripts/route.py が選んだ問題を1問ずつ。択一は答えたその場でサーバーが採点して解説を出す。
  // 記述は書いて保存し、最後に提出すると /grade-set が採点とレポートを作る
  let SET = null, setAt = 0, setPick = null, setConf = null, setGen = 0;
  const SLOT_HELP = { "洗い出し": "まだ測れていない論点", "穴埋め": "苦手な論点", "やり直し": "前に間違えた問題", "確認": "得意な論点の確認" };

  async function newSet(opts) {
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

  async function openSet(id, keepTab) {
    const gen = ++setGen;
    try {
      const { set } = await api(`/api/sets/${encodeURIComponent(id)}`);
      if (gen !== setGen) return;
      showSet(set);
      if (!keepTab) { showTab("study"); window.scrollTo({ top: 0 }); }
    } catch (e) { notice(esc(e.message), "error"); }
  }

  function showSet(s, at) {
    const same = SET && SET.id === s.id;
    SET = s;
    try { localStorage.setItem("learn.set", s.id); } catch (e) {}
    $("#set-card").hidden = false;
    $("#set-title").textContent = (s.kind === "choice" ? "択一ドリル " : "セット ") + s.id.replace(/^set-/, "");
    const label = { open: "解いている途中", submitted: "採点中", graded: "採点済み" }[s.status];
    $("#set-meta").textContent = `${s.phase}・${s.items.length}問・${label}`;
    const first = s.items.findIndex((it) => it.answer == null);
    setAt = at ?? (same ? setAt : first < 0 ? s.items.length : first);
    if (s.status === "open") timerSet(s.id, s.items.reduce((m, it) => m + (parseFloat(it.time_min) || (it.kind === "choice" ? 1 : 8)), 0));
    else if (tm.id === s.id) timerSet(null);
    drawSet();
  }

  function drawSet() {
    const s = SET;
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
    setConf = null;
    $("#set-reason").textContent = `${setAt + 1} / ${s.items.length}・${it.kind === "choice" ? "択一" : "記述"}・${it.slot}（${SLOT_HELP[it.slot] || ""}）・${it.reason}`;
    $("#set-body").innerHTML = md.render(it.body).html;
    const answered = it.kind === "choice" && !!it.result;
    const right = it.key && it.key.answer;
    $("#set-options").hidden = it.kind !== "choice";
    $("#set-short").hidden = it.kind !== "short";
    if (it.kind === "choice") {
      $("#set-options").innerHTML = it.options.map((o) => {
        const cls = answered ? (o.label === right ? "right" : o.label === it.answer ? "wrong" : "") : "";
        return `<button type="button" data-opt="${esc(o.label)}" class="${cls}"${answered || s.status !== "open" ? " disabled" : ""}><span class="lbl">${esc(o.label)}</span><span>${md.render(o.text).html.replace(/^<p>|<\/p>$/g, "")}</span></button>`;
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
      const c = it.result.confidence;
      const confNote = it.result.correct && (c === "guess" || c === "narrowed")
        ? `<p class="small">${c === "guess" ? "勘" : "消去法"}で選んだ問題です。当たっても理解の確認にはなりません。解説の各選択肢を読んで、理由を言えるか確かめましょう。</p>`
        : !it.result.correct && c === "sure" ? `<p class="small">根拠があると思って選んだ問題です。思い込みのずれがここにあります。解説の「${esc(it.answer)}」の行を読みましょう。</p>` : "";
      fb.innerHTML = `<p><b>${it.result.correct ? "正解" : "不正解"}</b>（あなたの答え：${esc(it.answer)}・正解：${esc(right || "")}）</p>` + confNote + md.render(it.key ? it.key.body : "").html;
    }
    $("#set-conf").hidden = it.kind !== "choice" || s.status !== "open" || answered;
    $$("#set-conf button").forEach((x) => x.classList.remove("sel"));
    $("#set-answer").hidden = s.status !== "open" || answered;
    $("#set-answer").textContent = it.kind === "choice" ? "答える" : "保存して次へ";
    $("#set-answer").disabled = it.kind === "choice";
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
    const it = SET && SET.items[setAt];
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
    const it = SET && SET.items[setAt];
    const term = e.target.term.value.trim();
    if (!it || !term) return;
    try {
      const r = await api(`/api/sets/${SET.id}/ask`, { method: "POST", body: { item: it.id, term } });
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
    const it = SET && SET.items[setAt];
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
    const b = e.target.closest("[data-opt]");
    if (!b || b.disabled) return;
    setPick = b.dataset.opt;
    $$("#set-options button").forEach((x) => x.classList.toggle("sel", x === b));
    $("#set-answer").disabled = !setConf;
    timerAutoStart();
  });
  $("#set-conf").addEventListener("click", (e) => {
    const b = e.target.closest("[data-conf]");
    if (!b) return;
    setConf = b.dataset.conf;
    $$("#set-conf button").forEach((x) => x.classList.toggle("sel", x === b));
    $("#set-answer").disabled = !setPick;
  });

  async function setAnswer() {
    const it = SET.items[setAt];
    const value = it.kind === "choice" ? setPick : $("#set-short-text").value;
    if (it.kind === "choice" && (!value || !setConf)) return;
    $("#set-answer").disabled = true;
    try {
      const body = { item: it.id, value };
      if (it.kind === "choice") body.confidence = setConf;
      const { set } = await api(`/api/sets/${SET.id}/answer`, { method: "POST", body });
      SET = set;
      resumeDeferred(set.id, it.id);
      if (it.kind === "short") setAt++;
      drawSet();
    } catch (e) { notice(esc(e.message), "error"); $("#set-answer").disabled = false; }
  }
  $("#set-answer").addEventListener("click", setAnswer);

  async function setMove(d) {
    const it = SET.items[setAt];
    // 書きかけの記述は、移るときに保存しておく
    if (it && it.kind === "short" && SET.status === "open" && $("#set-short-text").value !== (it.answer || "")) {
      try { SET = (await api(`/api/sets/${SET.id}/answer`, { method: "POST", body: { item: it.id, value: $("#set-short-text").value } })).set; }
      catch (e) { return notice(esc(e.message), "error"); }
    }
    setAt = Math.min(Math.max(setAt + d, 0), SET.items.length);
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
    const s = SET;
    const left = s.items.filter((it) => it.answer == null).length;
    const ch = s.items.filter((it) => it.kind === "choice" && it.result);
    const ok = ch.filter((it) => it.result.correct).length;
    $("#set-finish-text").innerHTML = s.status === "submitted"
      ? "提出しました。Claude が記述を採点し、解説と分析のレポートを書いています。下の会話欄に進み具合が出ます。" +
        "採点が途中で止まったときは「採点をやり直す」を押してください。"
      : `択一 ${ok} / ${ch.length}問 正解。` + (left ? `<b>まだ答えていない問題が ${left}問</b>あります（答えていない択一は記録しません。記述は0点になります）。` : "全部答えました。") +
        "提出すると、記述を採点し、問題ごとの解説と論点ごとの分析をレポートに保存します。";
    $("#set-submit").hidden = $("#set-back").hidden = s.status !== "open";
    $("#set-regrade").hidden = s.status !== "submitted";
  }
  // 提出後に採点の会話がエラーや停止で終わると、セットは「採点中」のまま残る。そこから採点だけをもう一度始める
  // （採点中なら、サーバーが「いま採点中です」と断る）
  $("#set-regrade").addEventListener("click", () => { if (SET) start({ action: "grade-set", id: SET.id }); });
  $("#set-submit").addEventListener("click", async () => {
    const left = SET.items.filter((it) => it.answer == null).length;
    if (!confirm(left ? `まだ答えていない問題が ${left}問あります。提出しますか？` : "提出して、採点とレポートを作りますか？")) return;
    try {
      const r = await api(`/api/sets/${SET.id}/submit`, { method: "POST", body: {} });
      if (tm.id === SET.id) timerPause(SET.id);
      showSet(r.set);
      openJob(r.job);
    } catch (e) { notice(esc(e.message), "error"); }
  });

  function drawReport() {
    const s = SET;
    const wrongOnly = $("#set-wrong-only").checked;
    $("#set-compare").innerHTML = (s.compare || []).length ? `<div class="table"><table><thead><tr><th>論点</th><th>前</th><th>後</th><th>判定</th></tr></thead><tbody>${
      s.compare.map((c) => `<tr><td>${esc(c.name)}</td><td>${pct(c.before)}</td><td>${pct(c.after)}</td><td class="small">${esc(c.status_before || "")} → ${esc(c.status_after)}</td></tr>`).join("")}</tbody></table></div>` : "";
    $("#set-report-body").innerHTML = md.render(s.report || "").html;
    $("#set-review").innerHTML = s.items.map((it, i) => ({ it, i })).filter(({ it }) => !wrongOnly || !(it.result && it.result.correct)).map(({ it, i }) => {
      const r = it.result;
      const mark = !r ? "—" : it.kind === "choice" ? (r.correct ? "○" : "×") : `${r.score} / ${r.max}`;
      return `<details class="review-item"><summary><b>${i + 1}.</b> ${mark}　${esc(it.concept_names.join("、"))} <span class="muted small">${it.kind === "choice" ? "択一" : "記述"}・${esc(it.slot)}</span></summary>
        <div class="md">${md.render(it.body).html}</div>
        ${it.options.length ? `<ul>${it.options.map((o) => `<li>${esc(o.label)} ${esc(o.text)}</li>`).join("")}</ul>` : ""}
        <p><b>あなたの答え：</b>${esc(it.answer == null ? "（無解答）" : it.answer)}</p>
        ${it.key ? `<div class="feedback md">${it.key.answer ? `<p><b>正解：${esc(it.key.answer)}</b></p>` : ""}${md.render(it.key.body).html}</div>` : ""}
      </details>`;
    }).join("") || `<p class="muted">間違えた問題はありません。</p>`;
  }
  $("#set-wrong-only").addEventListener("change", () => SET && drawReport());

  // 採点が終わったら講評を表示する
  async function showResult(id, jid) {
    const f = await fetchShown("results", id, jid);
    if (!f) return;
    currentQ = null;
    timerSet(null);
    $("#question-card").hidden = false;
    $("#question-title").textContent = "講評 " + id;
    $("#question-meta").textContent = "";
    $("#question-body").innerHTML = md.render(f.text).html;
    $("#answer-box").hidden = true;
  }

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
    if (!currentQ) return;
    if (!answerLoad || answerLoad.id !== currentQ || answerLoad.state !== "ok") {
      throw new Error(answerLoad && answerLoad.state === "loading" ? "解答を読み込み中です" : "解答を読み込めていないので保存できません");
    }
    let text = answerText();
    if (submit) {
      text = answer.setMeta(text, "submitted_at", nowIso());
      const ms = timerElapsed(currentQ);
      // タイマーを使わなかった（0に戻した）ときは前回の値を残さない。採点は started_at との差を使う
      text = ms > 0 ? answer.setMeta(text, "elapsed_min", Math.max(1, Math.round(ms / 60000))) : answer.delMeta(text, "elapsed_min");
    }
    await api(`/api/answers/${encodeURIComponent(currentQ)}`, { method: "PUT", body: { text } });
    $("#answer-status").textContent = "保存しました " + new Date().toLocaleTimeString();
  }

  // ---------------------------------------------------------- タイマー
  // 問題ごとに localStorage へ {acc: 止めるまでに貯めたミリ秒, since: 動かし始めた時刻} を持つ。
  // 同じブラウザなら再読み込みしても続きから測れる（別の端末とは共有しない）。別の問題や講評に移ると一時停止する。目安時間（time_limit_min）を過ぎたら赤くする
  const tm = { id: null, limit: null, warned: false, iv: null };
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

  function timerSet(id, limitMin) {
    if (tm.id && tm.id !== id) timerPause(tm.id); // 離れていた時間は数えない
    if (tm.id !== id) tm.warned = false;
    tm.id = id;
    tm.limit = limitMin ? limitMin * 60000 : null;
    clearInterval(tm.iv);
    tm.iv = id ? setInterval(timerRender, 1000) : null;
    timerRender();
  }
  function timerStart(id) { const t = tLoad(id); if (!t.since) { t.since = Date.now(); tSave(id, t); } }
  function timerPause(id) { const t = tLoad(id); if (t.since) { t.acc += Date.now() - t.since; t.since = null; tSave(id, t); } timerRender(); }
  function timerAutoStart() {
    if (!tm.id) return;
    const t = tLoad(tm.id);
    if (!t.since && !t.acc) { timerStart(tm.id); timerRender(); }
  }

  function timerRender() {
    const el = $("#timer");
    const visible = !!tm.id && !$("#tab-study").hidden && (!$("#question-card").hidden || (!!SET && tm.id === SET.id && !$("#set-card").hidden));
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
    if (!text || !job || !currentQ) return;
    if (job.status === "running") return notice("前の返事を待っています。届いてからもう一度どうぞ", "warn");
    try {
      const r = await api("/api/ask", { method: "POST", body: { job: job.id, id: currentQ, part: f.part.value, kind: f.kind.value, text } });
      $("#ask-text").value = "";
      job = r.job;
      setStatus(job);
      poll();
    } catch (err) { notice(esc(err.message), "error"); }
  });
  $("#save-answer").addEventListener("click", () => saveAnswer().catch((e) => notice(esc(e.message), "error")));
  $("#grade-btn").addEventListener("click", async () => {
    try {
      const qid = currentQ;
      await saveAnswer();
      if (!confirm("提出して採点しますか？") || currentQ !== qid) return;
      timerPause(qid);
      await saveAnswer(true);
      if (currentQ !== qid) return notice("提出の間に別の問題に切り替わったので、採点は始めませんでした", "warn");
      await start({ action: "grade", id: qid });
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

  async function loadConcepts() {
    const b = await api("/api/bank");
    const rows = Object.values(b.model || {});
    if (!rows.length) { $("#concepts-body").innerHTML = `<p class="muted">論点の一覧（exam/concepts.md）がまだありません。ホームの「問題バンクを作る」から作れます。</p>`; return; }
    rows.sort((x, y) => (STATUS_ORDER[x.status] ?? 2) - (STATUS_ORDER[y.status] ?? 2) || x.p - y.p);
    $("#concepts-body").innerHTML = `<div class="table"><table><thead><tr><th>論点</th><th>判定</th><th>見込み</th><th>解いた数</th></tr></thead><tbody>${
      rows.map((c) => `<tr class="st-${esc(c.status)}"><td>${esc(c.name)}<div class="muted small">${esc(c.concept)}</div></td><td><span class="badge">${esc(c.status)}</span></td>
        <td>${c.n ? `${pct(c.p)} <span class="muted small">(${pct(c.low)}〜${pct(c.high)})</span>` : "—"}</td><td>${c.n}</td></tr>`).join("")}</tbody></table></div>`;
  }

  async function loadStats() {
    loadConcepts().catch((e) => { $("#concepts-body").textContent = e.message; });
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
      <p class="muted small">質問しても点数や判定は変わりません。言葉の質問が多い分野は、択一ドリルと<a href="#" data-go="glossary">単語帳</a>で知識を固めるのがおすすめです。</p>`;
  }

  // ---------------------------------------------------------- 単語帳
  let G = { terms: [], pending: [] };
  async function loadGlossary() {
    G = await api("/api/glossary");
    const tagOpts = S ? S.tags.map((x) => `<option value="${esc(x.tag)}">${esc(x.name)}</option>`).join("") : "";
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

  // ---------------------------------------------------------- 履歴
  async function loadSets() {
    const { sets } = await api("/api/sets");
    const st = { open: "解いている途中", submitted: "採点中", graded: "レポートあり" };
    $("#sets-list").innerHTML = sets.length ? sets.map((s) => `<tr data-set="${esc(s.id)}"><td><a href="#">${esc(s.id)}</a></td>
      <td>${esc(s.phase || "")}</td><td>択一 ${s.correct} / ${s.choices}</td><td>${st[s.status] || s.status}</td></tr>`).join("")
      : `<tr><td colspan="4" class="muted">まだセットを解いていません</td></tr>`;
  }

  async function loadItems() {
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

  // ---------------------------------------------------------- 設定
  let profileDirty = false;
  $("#profile-text").addEventListener("input", () => { profileDirty = true; });
  let profileReq = 0;
  async function loadProfile(force) {
    if (profileDirty && !force) return; // 書きかけを消さない
    const req = ++profileReq;
    const { text } = await api("/api/profile");
    if (req !== profileReq) return; // あとから出した読み込みを優先する
    if (profileDirty && !force) return;
    profileDirty = false;
    $("#profile-text").value = text || "";
    $("#profile-text").placeholder = "まだありません。上でサンプルを選ぶか、対話で設定してください。";
  }
  $("#copy-example").addEventListener("click", () => useExample($("#example-select").value));
  async function useExample(key) {
    if (!key) return;
    const box = $("#profile-text");
    box.disabled = true; // コピーしている間の入力は置き換えで消えるので、止めておく
    try {
      try {
        await api("/api/setup-example", { method: "POST", body: { key } });
      } catch (e) {
        if (!/すでに/.test(e.message)) return notice(esc(e.message), "error");
        if (!confirm("いまの資格の設定（exam/profile.md）を置き換えますか？\n学習の記録は消えません。別の資格の記録と分けたいときは、キャンセルして「この資格を設定する」から対話で切り替えてください（古い記録を archive/ に移します）。")) return;
        await api("/api/setup-example", { method: "POST", body: { key, overwrite: true } });
      }
      await loadProfile(true);
      await loadState();
      $("#profile-status").textContent = "コピーしました。目標受験時期と学習者メモを書き換えて保存してください。";
      notice("資格を切り替えました。目標受験時期と学習者メモは<a href=\"#\" data-go=\"settings\">設定</a>で書き換えられます。");
      loadHome().catch(() => {});
    } catch (e) {
      notice(esc(e.message), "error");
    } finally {
      box.disabled = false;
    }
  }
  $("#setup-chat").addEventListener("click", () => start({ action: "setup" }));
  $("#save-profile").addEventListener("click", async () => {
    try {
      const sent = $("#profile-text").value;
      await api("/api/profile", { method: "PUT", body: { text: sent } });
      if ($("#profile-text").value === sent) profileDirty = false; // 保存中に書き足した分は未保存のまま
      $("#profile-status").textContent = "保存しました";
      loadState().catch((e) => notice(esc(e.message), "error"));
    } catch (e) { notice(esc(e.message), "error"); }
  });

  // ---------------------------------------------------------- 起動
  (async function init() {
    try { await loadState(); } catch (e) { notice(esc(e.message), "error"); return; }
    let tab = "home";
    try { tab = localStorage.getItem("learn.tab") || tab; } catch (e) {}
    if (S.profile !== "ok" || !$("#tab-" + tab)) tab = "home";
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
})();
