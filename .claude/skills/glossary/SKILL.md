---
name: glossary
description: 単語帳（log/glossary.jsonl）に言葉の意味を書き足す。解答中に意味を聞かれたまま意味が入っていない言葉や、指定された言葉の意味をまとめて作る。「単語帳」「用語の意味をまとめて」や /glossary で使う。
---

# 単語帳の意味を作る

引数：任意で言葉を「、」区切りで（例：`/glossary DMARC、SPF`）。

## 手順

1. 対象を集める
   - 引数の言葉
   - `log/asks.jsonl` の `kind: "word"` の質問のうち、`log/glossary.jsonl` のどの `term` も本文に含まないもの（1つの質問に複数の言葉があれば分ける。「〜の意味」などの言い回しは除いて言葉だけにする）
   - `log/glossary.jsonl` で `meaning` が空の言葉
   - 何もなければ「意味を作る言葉はありません」と伝えて終わる
2. 言葉ごとに意味を書く
   - 試験の分野（`exam/profile.md`）での一般的な意味を2〜3文で。略語は正式名称も書く。似た言葉との違いが紛らわしければ1文足す
   - 特定の問題の答えにつながる書き方（「この問題では〜」）はしない。採点前の問題の `rubrics/` は読まない
   - 分からない・自信がない言葉は Web で確かめる
3. `templates/glossary-line.json` の形で `log/glossary.jsonl` に1語1行で追記する
   - `source` は `glossary`、`from` は質問から作ったならその問題の id、`tags` はその問題の front matter の `tags`（引数だけの言葉は `exam/profile.md` の分野から選ぶ。分からなければ空）
   - 同じ言葉の行がすでにあっても、意味を書き直すなら新しい行を足す（後の行が使われる）
4. 作った言葉と意味の一覧を短く返す
