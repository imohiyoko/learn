---
name: grade
description: 学習者の解答を採点し、講評・成績記録・次の出題提案まで行う。「採点して」「解けた」「答え合わせ」や、チャットに解答が貼られたとき、/grade で使う。
---

# 採点

引数：任意で `<id>`。省略時は `answers/` のうち `results/<id>.md` がまだないものから、front matter の `started_at` が最も新しいものを対象にする。`drill-*`（択一ドリル）は対象にしない（`/drill` の中で答え合わせする）。

## 手順

1. **解答をそろえる**
   - `questions/<id>.md` と `rubrics/<id>.md` がなければ採点せず、その旨を伝えて終わる
   - チャットに解答が貼られていたら、`answers/<id>.md` の該当設問に書き写して保存する
   - 解答が画像（手書きの写真、スクリーンショット、図を描いた答え）なら：
     - 画像ファイルがあれば `answers/<id>-<n>.<拡張子>` に置く（チャットに貼られただけの画像は保存できないので省略してよい）
     - 文字を `answers/<id>.md` に書き起こす。読めない字は推測で補わず `〔判読不能〕` とする
     - 書き起こし全文を学習者に見せ、読み違いがないか、`〔判読不能〕` の箇所に何と書いたかを確認してから採点に進む
     - 図で答える設問（構成図を描く、矢印を引くなど）は、図の要素（何と何がどうつながっているか）を文章で書き起こし、画像のパスも添える
   - `submitted_at` が空なら現在時刻を入れる。front matter に `elapsed_min`（ブラウザのタイマーで測った分。一時停止した時間は含まない）があればそれを所要時間（分）とし、なければ `started_at` との差を所要時間とする
   - 空欄の設問は「無解答」として扱う（勝手に補わない）

2. **採点を任せる**
   - `grader` エージェントに次の4つのパスを渡す：`exam/profile.md`、`questions/<id>.md`、`rubrics/<id>.md`、`answers/<id>.md`。解答の画像を置いたなら、そのパスも渡す
   - `log/asks.jsonl` にこの `<id>` の行（解答中の質問）があれば、設問・種類・本文を箇条書きで一緒に渡す。点数には使わず、ミスの種類の判断にだけ使ってもらう
   - grader は設問ごとの得点・判定・不足要素・ミスの種類（knowledge / reading / writing）を返す

3. **結果を保存**
   - `templates/result.md` の形で `results/<id>.md` を作る
   - 講評は短く。各設問で「足りなかったもの」と「本文の根拠」を必ず書く
   - 解答例は rubric から転記してよい（採点後なので公開してよい）

4. **成績を記録**
   - `templates/history-line.json` の形式で `log/history.jsonl` に1行（改行を含まないJSON）追記する。各項目の出どころ：
     - `id`・`source`・`tags`：`questions/<id>.md` の front matter から。`tags` は順番を変えない（先頭が主タグ）
     - 問題バンクから出した長文（front matter に `concepts` がある）なら、`concepts`・`difficulty` もそのまま入れ、`kind: "long"` を足す（`scripts/route.py` が論点ごとの見込みに使う）
     - `score`・`max`：grader の合計点と満点（数値で）
     - `minutes`：手順1で出した所要時間（数値、分）
     - `graded_at`：現在時刻（ISO8601）
     - `weak`：具体的な弱点（用語名や読み落とした観点）を最大3つ。解答中に意味を聞いた言葉が、失点した設問に関わっていれば優先して入れる
     - `miss_type`：grader が返したミスの種類の件数（数値で）
   - 追記後に `python3 scripts/stats.py` を実行し、警告が出ないことを確認する
   - grader が `knowledge` とした設問で足りなかった用語（rubric の必須要素の用語）を、`templates/glossary-line.json` の形で `log/glossary.jsonl` に追記する（`source: "grade"`、`from: <id>`、意味は2〜3文の一般的な説明）。採点後なので rubric を元にしてよい。同じ言葉で意味が入った行がすでにあれば足さない

5. **学習者に返す**
   - 合計点、合否ライン（profile の合格基準）との差、設問別の表を表示
   - 弱点を1〜2行でまとめる
   - `python3 scripts/stats.py --next` を実行し、次の推奨タグを一行で提案する：
     「次は `<tag>`（<分野名>）がおすすめです。続けますか？」
   - 学習者が続けると答えたら `question` スキルを実行する

## 注意

- 採点に納得できないと言われたら、grader の判定理由と rubric を示したうえで、学習者の主張が rubric の別解に当たるか検討する。当たるなら rubric に別解を追記し、点数を直して log も修正する
- 採点を甘くしない。部分点は rubric の要素単位でのみ与える
