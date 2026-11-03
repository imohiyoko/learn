#!/usr/bin/env python3
"""テスト用の偽の claude。`claude -p ... --output-format stream-json` と同じ形で出力する。

/question なら questions/ と answers/ にファイルを作り、そのことをツール呼び出しとして出す。
"""
import json
import sys
from pathlib import Path

args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
resume = args[args.index("--resume") + 1] if "--resume" in args else None
sid = resume or "fake-session-1"


def out(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


out({"type": "system", "subtype": "init", "session_id": sid})
content = []
if prompt.startswith("/question"):
    qid = "20261103-1200-web"
    q = Path("questions") / f"{qid}.md"
    q.parent.mkdir(exist_ok=True)
    q.write_text(f"---\nid: {qid}\ntags: [web]\nverified: true\ntime_limit_min: 60\n---\n\n# 問題\n\n本文です。\n\n## 設問1\n\n答えなさい。\n", encoding="utf-8")
    Path("answers").mkdir(exist_ok=True)
    (Path("answers") / f"{qid}.md").write_text("---\nstarted_at: now\n---\n\n## 設問1\n\n", encoding="utf-8")
    Path("rubrics").mkdir(exist_ok=True)
    (Path("rubrics") / f"{qid}.md").write_text("秘密の採点基準", encoding="utf-8")
    content.append({"type": "tool_use", "name": "Write", "input": {"file_path": str((Path.cwd() / "rubrics" / f"{qid}.md")), "content": "秘密の採点基準"}})
    content.append({"type": "tool_use", "name": "Write", "input": {"file_path": str(Path.cwd() / q), "content": "..."}})
    content.append({"type": "text", "text": f"`{qid}` を出題しました。"})
elif prompt.startswith("/grade "):
    gid = prompt.split()[1]
    Path("results").mkdir(exist_ok=True)
    (Path("results") / f"{gid}.md").write_text("# 採点結果\n\n| 設問 | 得点 |\n|---|---|\n| 1 | 60 |\n", encoding="utf-8")
    content.append({"type": "text", "text": "60点でした。"})
else:
    content.append({"type": "text", "text": f"受け取りました：{prompt}" + (f"（{resume} の続き）" if resume else "")})
if prompt.startswith("/question"):
    # verifier の出力（解答）はブラウザに流れてはいけない
    out({"type": "assistant", "session_id": sid, "parent_tool_use_id": "toolu_x",
         "message": {"content": [{"type": "text", "text": "verifierの解答：秘密"}]}})
out({"type": "assistant", "session_id": sid, "message": {"content": content}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "session_id": sid,
     "duration_ms": 1200, "total_cost_usd": 0.01})
