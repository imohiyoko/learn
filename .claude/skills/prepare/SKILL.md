---
name: prepare
description: 問題バンク（bank/）を前もって作る。論点の一覧を作り、論点ごとに択一と短い記述、分野ごとに本番形式の長文を、採点基準つき・verifier の確認つきで貯める。「問題を準備して」「バンクを作って」「ストック」や /prepare で使う。
---

# 問題バンクの準備

引数：任意で分野タグと、今回作る問題数の上限（例：`/prepare`、`/prepare web`、`/prepare web 12`）。上限の既定は 20。足りない分は次の回に作る（`/loop` で回していれば少しずつ埋まる）。

どの問題を出すかは `scripts/route.py` が機械的に決める。このスキルは「選べる問題を用意する」だけで、出題の順番には関わらない。

## 手順

1. **論点の一覧**
   - `exam/profile.md` を読む。ない、または TODO が残っていれば `/setup-exam` を案内して終わる
   - `exam/concepts.md` がなければ、`templates/concepts.md` の形で作る。分野タグごとに論点を4〜8個。`refs/syllabus/` にシラバスがあれば、それに沿って分ける。論点は「1問の択一で1つ問える」くらいの粒度にする
   - 作ったら（または論点を足したら）、論点の一覧を返事の最初に短く示す。学習者は `exam/concepts.md` を直してよい
2. **足りない数を調べる**
   - `python3 scripts/route.py --stock --json` を実行する。論点ごとの `need`（択一 `choice`・短い記述 `short`）と、分野ごとの長文 `long` の `need` が、作る数
   - 引数にタグがあれば、その分野だけ。足りない論点が多いときは、`python3 scripts/route.py --model --json` で見込みが低い・まだ測れていない論点を先にする
   - `unverified`（確認待ち）の問題があれば、新しく作る前に手順4だけ済ませる
3. **作る**（上限の数まで。正解キーを先に作る）
   - id：択一 `b-<タグ>-<論点の語>-c<番号>`、短い記述 `-s<番号>`、長文 `b-<タグ>-l<番号>`。同じ id のファイルがあれば番号を進める
   - 択一：`templates/bank/choice-key.md` の形で `bank/keys/<id>.md` を書いてから、`templates/bank/choice.md` の形で `bank/items/<id>.md` を書く（`verified: false`）。1つの論点の3問は難しさ 1・2・3 を1問ずつにし、問う角度（意味・仕組み・当てはめ）を変える。選択肢の作り方は `/drill` と同じ（正解は1つ、誤りは「よくある誤解」「似た用語」「条件違い」、「すべて正しい」は使わない、正解の位置を偏らせない）
   - 短い記述：`templates/bank/short-key.md` → `templates/bank/short.md`。5〜10行の事例と設問1つ、20〜60字、10点。事例に根拠を必ず書く
   - 長文：`/question` の手順2〜3と同じ作り方で、正解キーは `templates/rubric.md` の形、問題は `templates/question.md` の形にする。保存先は `bank/keys/<id>.md` と `bank/items/<id>.md`。front matter に `kind: long`、`concepts: [<この問題で問う論点ID。2〜4個>]`、`difficulty: 2`、`time_min`（profile の目安時間）を足す
   - 架空の組織・システムで作る。実在企業名や過去問本文の転載はしない
4. **確かめる**
   - `verifier` エージェントに `bank/items/` の問題のパスを、5問ずつまとめて渡す（`bank/keys/` は渡さない）
   - 答えがキーと違う、確信度が低い、正解が複数ありうる、と返った問題は直す。直せなければ消す（`bank/items/` と `bank/keys/` の両方）
   - 通った問題は `verified: true` にする
5. **返す**
   - 作った数（論点別）、消した数、まだ足りない数（`python3 scripts/route.py --stock` の最後の行）を短く
   - 足りない数が 0 なら「問題バンクがそろいました。ホームの『今日のセット』から始められます」

## 注意

- `bank/keys/` は正解と採点基準。学習者に見せない・返事に書かない（`rubrics/` と同じ扱い）
- 一度出題した問題も消さない（成績の記録が参照する）。直すときは同じ id のまま書き換えず、新しい id で作り直す
