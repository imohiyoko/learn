---
name: grade-set
description: 問題バンクのセット（sets/<set>）を提出後にまとめて採点し、問題ごとの解説と論点ごとの知識の分析をレポートにして reports/<set>.md に保存する。web UI でセットを提出したときや /grade-set で使う。
---

# セットの採点とレポート

引数：`<set>`（例：`/grade-set set-20261110-120000`）。

択一は、学習者が答えたその場でサーバーが採点し、`log/bank.jsonl` に記録済み。このスキルは短い記述を採点し、セット全体の解説と分析を書く。

## 手順

1. **そろえる**
   - `sets/<set>/set.json` を読む（`items`：問題と、選ばれた理由 `slot`・`reason`、`answers`：学習者の答え、`results`：択一の正誤）
   - 問題は `bank/items/<id>.md`、正解・採点基準は `bank/keys/<id>.md`、記述の答えは `sets/<set>/answers/<id>.md`
   - `reports/<set>.md` がすでにあり、`log/bank.jsonl` にこのセットの記述の行もあれば、採点し直さずに「レポートはできています」とだけ返す
2. **記述を採点する**（短い記述 `kind: short` ごとに）
   - `grader` エージェントに `exam/profile.md`、`bank/items/<id>.md`、`bank/keys/<id>.md`、`sets/<set>/answers/<id>.md` のパスを渡す。答えのファイルがなければ無解答（0点）
   - `log/asks.jsonl` にこの問題の質問があれば一緒に渡す（ミスの種類の判断にだけ使う）
   - `templates/bank-line.json` の形で `log/bank.jsonl` に1問1行追記する：`id` は `<set>-<問題id>`、`set`・`item`・`kind`・`tags`・`concepts`・`difficulty` は set.json から、`score`・`max`・`miss_type`・`weak` は grader から、`graded_at` は現在時刻
   - 答えていない択一は記録しない（出したが解かなかった問題）
3. **分析の材料を集める**
   - `python3 scripts/route.py --compare <set> --json`：このセットの前後の、論点ごとの見込み
   - `python3 scripts/route.py --model --json`：いまの論点ごとの見込み
   - `log/bank.jsonl` `log/history.jsonl` で、同じ論点・同じ弱点の過去の記録
   - `log/asks.jsonl` `log/glossary.jsonl` の関係する言葉
   - `python3 scripts/route.py --json`：次のセットで選ばれる論点（「次に埋める穴」に使う）
4. **レポートを書く**：`templates/report.md` の形で `reports/<set>.md`
   - 問題ごとの解説は、正解の根拠に加えて、間違えた問題は「選んだ答え・書いた答えのどこがずれているか」を書く。正解した問題は1〜2行でよい
   - 知識の分析は、このセットだけでなく過去の記録と合わせて「同じ論点で何回目か」「間違い方が同じか」を見る。根拠のない推測は書かない
   - 「次に埋める穴」は route.py が次に選ぶ論点に合わせる（レポートで別の順番を勧めない）
5. **単語帳**：知識不足（`knowledge`）で落とした問題の用語を、`templates/glossary-line.json` の形で `log/glossary.jsonl` に追記する（`source: "grade"`、`from: <set>`）。同じ言葉で意味の入った行があれば足さない
6. **返す**：択一の正解数、記述の得点、いちばん大きい穴を1行、「レポートは画面の『レポート』から見返せます」

## 注意

- 採点を甘くしない。部分点は採点基準の要素単位でのみ
- 正解キーの内容は、採点が終わったこのセットの問題についてだけ書いてよい。ほかの（まだ解いていない）バンクの問題の正解には触れない
