// learn web UI の settings.js：設定（exam/profile.md）。
// サーバー（webui/server.py）の /api を呼ぶだけの素の JavaScript（ES モジュール。ビルドは要らない）。
import { $, api, esc, notice } from "./util.js";
import { loadState } from "./main.js";
import { start } from "./study.js";
import { loadHome } from "./home.js";

// ---------------------------------------------------------- 設定
let profileDirty = false;
$("#profile-text").addEventListener("input", () => { profileDirty = true; });
let profileReq = 0;
export async function loadProfile(force) {
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
export async function useExample(key) {
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
    notice("資格を切り替えました。目標受験時期と学習者メモは<a href=\"/settings\">設定</a>で書き換えられます。");
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
