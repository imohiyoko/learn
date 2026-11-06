# ファイルと記録の形

スキル・スクリプト・web UI（`webui/`）が読み書きするファイルの約束を、ここに一本化する。
スキルの SKILL.md には手順だけを書き、ファイルの形はここ（と `schema/`・`templates/`）を指す。
形を変えるときは、この表の「読む」側をすべて直す。

## 記録（`log/`）

1行1件の JSON Lines。改行を含まない JSON を1行ずつ追記する。各行の形は `schema/<名前>.schema.json` が正で、
`templates/<名前>.json` はその例。`tests/test_contract.py` が、例と web UI の書く行がスキーマに合うかを確かめる。

| ファイル | 1行の形 | 書く | 読む |
|---|---|---|---|
| `log/history.jsonl` | `schema/history-line.schema.json` | `/grade` | `scripts/stats.py`・`scripts/analyze.py`・`scripts/route.py`（`concepts` のある行）・web UI・`/review`・`/inbox`・imohiyoko/learning の `learning import history` |
| `log/bank.jsonl` | `schema/bank-line.schema.json` | web UI（択一を答えたとき）・`/drill`・`/grade-set`（短い記述） | `scripts/route.py`・`scripts/analyze.py`・`/grade-set`・`/inbox` |
| `log/drill.jsonl` | `schema/drill-line.schema.json` | いまは書かない（以前の択一ドリル） | `scripts/analyze.py`・web UI |
| `log/asks.jsonl` | `schema/ask-line.schema.json` | web UI（解答中の質問） | `scripts/analyze.py`・`/grade`・`/grade-set`・`/glossary`・web UI |
| `log/glossary.jsonl` | `schema/glossary-line.schema.json` | `/glossary`・`/grade`・`/grade-set`・`/inbox`・web UI | web UI・`/prepare`・`/glossary`・`/grade`・`/grade-set`・`/inbox`（同じ言葉の行がないかを見る） |
| `log/glossary-state.json` | `{ <言葉の正規形>: {known, reviews, misses, last} }` | web UI（「覚えた」・復習） | web UI・`/prepare` |

- 時刻はタイムゾーン付きの ISO 8601（例 `2026-10-25T22:20:00+09:00`）
- `log/history.jsonl` は imohiyoko/learning も取り込む。learning は知らない項目のある行を読まないので、
  項目を足すときは learning の `internal/practice/history.go` を直す PR を組にする（learning の ADR 0033）

## 問題と解答（Markdown）

| ファイル | 形 | 書く | 読む |
|---|---|---|---|
| `questions/<id>.md` | `templates/question.md`。front matter の `id`・`source`・`tags`、本文の `## 設問` | `/question`・`/import-past` | web UI・`/grade`・grader |
| `rubrics/<id>.md` | `templates/rubric.md` | `/question`（バンクからコピー）・`/import-past` | `/grade`・grader。**採点が終わるまで学習者に見せない** |
| `answers/<id>.md` | `templates/answer.md`。`## 設問N` / `(n)` の見出しで欄を分ける | 学習者・web UI | `/grade`・web UI |
| `results/<id>.md` | `templates/result.md` | `/grade` | 学習者・web UI |
| `bank/items/<id>.md` | `templates/bank/choice.md`・`templates/bank/fill.md`・`templates/bank/short.md`。front matter の `kind`・`tags`・`concepts`・`difficulty`・`verified` | `/prepare`・web UI の問題ページ（tags・concepts・difficulty・本文の編集。本文を変えたら `verified: false`） | `scripts/route.py`・web UI・`/question`・`/drill`・verifier |
| `bank/keys/<id>.md` | `templates/bank/choice-key.md`・`templates/bank/fill-key.md`・`templates/bank/short-key.md` | `/prepare`・web UI の問題ページ（編集したら問題を `verified: false` に） | web UI（択一・穴埋めの答え合わせ）・`/grade-set`・grader。**答えるまで見せない**（セットの解答画面では答えるまで出さない。例外は問題の管理画面 `/bank/<id>` で、「正解を見る」か編集を押したときだけ出す） |
| `bank/trash/items/<id>.md`・`bank/trash/keys/<id>.md` | 削除した問題（ゴミ箱。`bank/items` と同じ形） | web UI の問題ページ | web UI（戻すとき）。route.py は読まない |
| `sets/<set>/set.json` | セットの問題と答え | web UI | `/grade-set`・web UI |
| `reports/<set>.md` | `templates/report.md` | `/grade-set` | 学習者・web UI |
| `exam/profile.md` | `templates/profile.md`。「分野タグ」「択一問題」「穴埋め問題」の節 | `/setup-exam` | すべてのスキル・`scripts/`・web UI |
| `exam/concepts.md` | `templates/concepts.md`。`\| タグ \| 論点ID \| 論点 \| 重み \|` の表 | `/prepare` | `scripts/route.py`・web UI |

web UI が本文から読み取る書き方：

- 解答欄：問題の `## 設問` の節にある `**設問N**`・`(n)`・`N字以内`（`webui/static/answer.js`）
- 択一の選択肢：`- ア <選択肢>` の形の行（`learn/web/sets.py` の `CHOICE_RE`）
- 穴埋めの空欄：問題文の全角の `［ア］`。正解キーの「## 解説」より前の `- ア：<正解>｜<別解>` の行（`learn/fill.py`）。答えはカンマ・「円」・全角半角・△▲ をそろえて比べる
- 解答中の質問の設問：`設問N` か `設問N(n)`（`learn/web/glossary.py` の `PART_RE`）

## スキルを足す・変えるとき

- `.claude/skills/<名前>/SKILL.md` の front matter に `name`（フォルダ名と同じ）と `description` を書く
- CLAUDE.md の「スキル」の一覧に足す
- スクリプトを呼ぶなら、web UI から呼ばれたときに使えるよう `learn/web/jobs.py` の `ALLOWED_TOOLS` に入っているか確かめる
- `tests/test_skills.py` が、この3つと、スキルの書いているファイル・スクリプトの引数が実際にあるかを確かめる

## 択一の「不安」の印と確信度（`unsure`・`confidence`）

- 択一を答えるとき、画面で自信のない選択肢に「？」を付けられる（任意。選んだ答えにも付けられる）。付けた記号を `log/bank.jsonl` の1行に `unsure` として書く。別ファイルにはしない
- `confidence` はサーバーが `unsure` から決める：選んだ答えに印があれば `guess`、ほかの選択肢だけなら `narrowed`。印がなければ `unsure` も `confidence` も書かない（付け忘れもあるので「根拠がわかる」とは決めつけない）
- `sure` は、以前の画面（3択で確信度を選ばせていたころ）の行にだけある
- 確信度のない行（印なし、この機能より前の記録、`/drill` の記録）は「不明」として扱う。あとから書き足さない
- `scripts/route.py` の見込みでは、○でも `narrowed`・`guess` は動かす強さを下げ（0.6・0.3）、×で `sure` は強く（1.3）、×で `guess` は弱く（0.7）効かせる。不明は 1.0（`learn/route.py` の `CONF_WEIGHT_*`）
- `scripts/analyze.py`（分野ごとの分析）は、いまは確信度を使わない
