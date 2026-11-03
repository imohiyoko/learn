---
name: question
description: 記述式の練習問題を1問出題する。問題バンクの長文（採点基準つき・確認済み）から route.py が選んだものを出す。「出題して」「問題ちょうだい」「次」「続けて」や /question で使う。
---

# 出題

引数：任意で分野タグ（例：`/question web`）、または `past-<年度>-<問番号>`（`/import-past` で取り込み済みの過去問。例：`/question past-r06a-q1`）。

問題はその場で作らない。`/prepare` で前もって作り、verifier の確認を通した問題バンク（`bank/`）の長文から、`scripts/route.py` が選んだものを出す（自分で選び直さない）。バンクに長文がなければ知らせて終わる。作り足すのは学習者が好きなときに `/prepare` で行う。

## 手順

1. **前提を読む**
   - `exam/profile.md` を読む。ファイルがない、または `<!-- TODO -->` が残っていたら出題せず `/setup-exam` を案内して終わる
   - 引数が `past-...` なら `questions/<id>.md` と `rubrics/<id>.md` があることを確認して**手順3へ飛ぶ**（公式の過去問は検証不要）。なければ `/import-past` を案内して終わる
2. **バンクから出す**
   - `python3 scripts/route.py --kinds long --json`（引数でタグ指定があれば `--tag <タグ>` を付ける）
   - `items` が空なら「問題バンクに（タグの指定があればその分野の）長文がありません。`/prepare <タグ>`（または画面の『問題バンクを作る』）で作ってから、もう一度どうぞ」と返して終わる。その場で問題を作らない
   - `items` の問題を出す：
     - `<id>` を決める：`YYYYMMDD-HHMM-<主タグ>`（時刻は `date +%Y%m%d-%H%M` で取得）
     - `bank/items/<バンクのid>.md` を `questions/<id>.md` に、`bank/keys/<バンクのid>.md` を `rubrics/<id>.md` にコピーする。`questions/<id>.md` の front matter の `id` を `<id>` に、`issued_at` を現在時刻にし、`bank_item: <バンクのid>` を足す（`concepts` `difficulty` はそのまま残す）
     - `bank/items/<バンクのid>.md` の front matter に `issued_to: <id>` を足す（同じ長文を二度出さないため）

3. **学習者に渡す**
   - `templates/answer.md` を元に `answers/<id>.md` を作り、`started_at` に現在時刻、設問の見出しを問題に合わせて入れる
   - 問題文（`questions/<id>.md` の本文と設問）を表示する。rubric の内容や verifier の結果には触れない
   - 最後に一行で案内する：「`answers/<id>.md` に書くか、このチャットに貼ってください。終わったら `/grade`」
