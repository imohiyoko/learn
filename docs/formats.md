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
| `bank/items/<id>.md` | `templates/bank/choice.md`・`templates/bank/short.md`。front matter の `kind`・`tags`・`concepts`・`difficulty`・`verified` | `/prepare` | `scripts/route.py`・web UI・`/question`・`/drill`・verifier |
| `bank/keys/<id>.md` | `templates/bank/choice-key.md`・`templates/bank/short-key.md` | `/prepare` | web UI（択一の答え合わせ）・`/grade-set`・grader。**答えるまで見せない** |
| `sets/<set>/set.json` | セットの問題と答え | web UI | `/grade-set`・web UI |
| `reports/<set>.md` | `templates/report.md` | `/grade-set` | 学習者・web UI |
| `exam/profile.md` | `templates/profile.md`。「分野タグ」「択一問題」の節 | `/setup-exam` | すべてのスキル・`scripts/`・web UI |
| `exam/concepts.md` | `templates/concepts.md`。`\| タグ \| 論点ID \| 論点 \| 重み \|` の表 | `/prepare` | `scripts/route.py`・web UI |

web UI が本文から読み取る書き方：

- 解答欄：問題の `## 設問` の節にある `**設問N**`・`(n)`・`N字以内`（`webui/static/answer.js`）
- 択一の選択肢：`- ア <選択肢>` の形の行（`learn/web/sets.py` の `CHOICE_RE`）
- 解答中の質問の設問：`設問N` か `設問N(n)`（`learn/web/glossary.py` の `PART_RE`）

## スキルを足す・変えるとき

- `.claude/skills/<名前>/SKILL.md` の front matter に `name`（フォルダ名と同じ）と `description` を書く
- CLAUDE.md の「スキル」の一覧に足す
- スクリプトを呼ぶなら、web UI から呼ばれたときに使えるよう `learn/web/jobs.py` の `ALLOWED_TOOLS` に入っているか確かめる
- `tests/test_skills.py` が、この3つと、スキルの書いているファイル・スクリプトの引数が実際にあるかを確かめる
