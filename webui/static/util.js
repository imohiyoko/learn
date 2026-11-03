// learn web UI の util.js：共通の部品：DOM の取り出し、/api の呼び出し、お知らせ、モジュールをまたぐ状態（st）。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。

// モジュールをまたいで読み書きする状態
export const st = {
  S: null, // /api/state
  job: null, // いま表示している会話
  seq: 0,
  currentQ: null,
  noticeOwner: null,
  SET: null, // いま開いているセット
};

export const $ = (s) => document.querySelector(s);
export const $$ = (s) => Array.from(document.querySelectorAll(s));
export const esc = md.esc;

export async function api(path, opts = {}) {
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
export function notice(msg, kind, owner) {
  const n = $("#notice");
  st.noticeOwner = msg ? owner || null : null;
  if (!msg) { n.hidden = true; return; }
  n.hidden = false;
  n.className = "notice " + (kind || "");
  n.innerHTML = msg;
}
