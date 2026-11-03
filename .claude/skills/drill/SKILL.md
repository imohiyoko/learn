---
name: drill
description: 問題バンクの択一を数問まとめて出題し、答え合わせ・解説・記録まで行う。知識の確認や択一式の試験対策、「択一やりたい」「選択問題」「小テスト」や /drill で使う。
---

# 択一ドリル

引数：任意で分野タグと問題数（例：`/drill`、`/drill <タグ>`、`/drill <タグ> 5`）。

問題はその場で作らない。`/prepare` で前もって作り、verifier の確認を通した問題バンク（`bank/`）の択一から、`scripts/route.py` が選んだものだけを出す（自分で選び直さない）。在庫が足りなければ、ある分だけ出して知らせる。作り足すのは学習者が好きなときに `/prepare` で行う。

ブラウザの画面では、サーバーが同じ選び方で択一だけのセットを作るので、このスキルは使わない（チャットで解くとき用）。

## 手順

1. **前提を読む**
   - `exam/profile.md` を読む。ファイルがない、または `<!-- TODO -->` が残っていたら `/setup-exam` を案内して終わる
   - 問題数は引数、なければ profile の「択一問題」の節の1回の問題数、なければ 10。30 を超えるなら 30
2. **選ぶ**
   - `python3 scripts/route.py --kinds choice --n <問題数> --json`（タグの指定があれば `--tag <タグ>`）
   - `items` が空なら「問題バンクに択一がありません。`/prepare`（または画面の『問題バンクを作る』）で作ってから、もう一度どうぞ」と返して終わる
   - `items` が問題数より少なければ、ある分で続け、最後に「在庫が足りないので N問だけ出しました。`/prepare` で作り足せます」と添える
3. **セットとして保存する**（画面の履歴とレポートから見返せるように、サーバーと同じ形で残す）
   - セットの id は `set-YYYYMMDD-HHMMSS`（`date +%Y%m%d-%H%M%S`）。`sets/<id>/` があれば末尾に `-2` などを付ける
   - `sets/<id>/set.json` に書く：`{"id", "created": 現在時刻, "tag": タグか null, "kind": "choice", "want": 問題数, "phase": route.py の phase, "status": "open", "items": route.py の items そのまま, "answers": {}, "results": {}, "shortage": route.py の shortage}`
4. **学習者に渡す**
   - `bank/items/<id>.md` の本文と選択肢だけを、番号を振って表示する。`bank/keys/` の内容・選ばれた理由の中の正解につながる情報には触れない
   - 最後に一行で案内する：「`1ア 2ウ 3エ …` のようにまとめて答えてください（分からない問題は `?`）」
5. **答え合わせ**（機械的に。正解キーを後から変えない）
   - 各問を `bank/keys/<id>.md` の front matter の `answer` と照らす。`?` と空欄は不正解
   - 学習者が「この選択肢も正しいのでは」と言い、根拠を確かめて本当に正解が複数ありうるなら、その問題は記録せず、`bank/items/<id>.md` を `verified: false` に戻して理由を `bank/keys/<id>.md` に書いておく（次の `/prepare` で直す）
6. **記録**
   - 答えた問題ごとに1行、`log/bank.jsonl` に追記する（`templates/bank-line.json` の形。改行を含まないJSON）
     - `id`：`<セットid>-<問題id>`、`set`・`item`・`kind`（`choice`）・`tags`・`concepts`・`difficulty`：set.json の items から
     - `score`：正解なら1、不正解なら0、`max`：1、`answer`：選んだ記号、`graded_at`：現在時刻
     - `weak`：不正解なら主の論点の名前（`exam/concepts.md`）を1つ。正解なら空リスト
     - `miss_type`：不正解なら `knowledge`（知らなかった）か `reading`（読み違えた）のどちらか1件。正解なら空オブジェクト
   - `sets/<id>/set.json` の `answers` に `{問題id: 記号}`、`results` に `{問題id: {"correct": true/false, "graded_at": 現在時刻}}` を入れ、`status` を `submitted`、`submitted_at` を現在時刻にする
7. **レポート**：`/grade-set <セットid>` の手順3〜5で `reports/<セットid>.md` を書く（記述がないので手順2は不要）
8. **学習者に返す**
   - 正答数 / 問題数、間違えた問題の正解と根拠（`bank/keys` の解説から1〜2行）
   - 「続けますか？」と聞き、続けるなら `/drill` をもう一度実行する

## 公式の過去問の択一

公式の過去問の択一を択一で解きたいときは、`refs/` の問題冊子から `bank/items/b-past-<年度>-a<番号>.md`（`templates/bank/choice.md` の形。`verified: true`、`concepts` は `exam/concepts.md` から当てはまる論点）と `bank/keys/b-past-<年度>-a<番号>.md`（`answer` は公式の解答だけを使う）に取り込む。取り込んだ問題は、ほかのバンクの問題と同じく route.py が選ぶ。
