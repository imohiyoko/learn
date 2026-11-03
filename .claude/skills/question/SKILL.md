---
name: question
description: 記述式の練習問題を1問出題する。採点基準を先に作って保存し、verifier で解答可能性を検証してから問題を見せる。「出題して」「問題ちょうだい」「次」「続けて」や /question で使う。
---

# 出題

引数：任意で分野タグ（例：`/question web`）、または `past-<年度>-<問番号>`（`/import-past` で取り込み済みの過去問。例：`/question past-r06a-q1`）。

## 手順

1. **前提を読む**
   - `exam/profile.md` を読む。ファイルがない、または `<!-- TODO -->` が残っていたら出題せず `/setup-exam` を案内して終わる
   - 引数が `past-...` なら `questions/<id>.md` と `rubrics/<id>.md` があることを確認して**手順5へ飛ぶ**（過去問は検証不要）。なければ `/import-past` を案内して終わる
   - それ以外は、まず問題バンクに用意した長文を探す：`python3 scripts/route.py --kinds long --json`（引数でタグ指定があれば `--tag <タグ>` を付ける）。`items` に問題があれば、それを出す（自分で選び直さない）：
     - `<id>` を手順2と同じ決め方で決め、`bank/items/<バンクのid>.md` を `questions/<id>.md` に、`bank/keys/<バンクのid>.md` を `rubrics/<id>.md` にコピーする。`questions/<id>.md` の front matter の `id` を `<id>` に、`issued_at` を現在時刻にし、`bank_item: <バンクのid>` を足す（`concepts` `difficulty` はそのまま残す）
     - `bank/items/<バンクのid>.md` の front matter に `issued_to: <id>` を足す（同じ長文を二度出さないため）
     - バンクの問題は確認済みなので、**手順5へ飛ぶ**
   - バンクに長文がなければ `python3 scripts/stats.py --next` を実行し、推奨タグを得て（引数でタグ指定があればそれを優先）、手順2から作る

2. **採点基準を先に作る**
   - まず `<id>` を決める：`YYYYMMDD-HHMM-<主タグ>`（時刻は `date +%Y%m%d-%H%M` で取得）
   - 主タグ（と必要なら副タグ1つ）で、`exam/profile.md` の出題形式に沿った問題を設計する
   - 架空の組織・システムで作る。実在企業名や過去問本文の転載はしない
   - 設問ごとに「本文のどこを読めば答えが決まるか」を先に決め、本文にその根拠を必ず書き込む
   - 知識だけで解ける設問は全体の3割まで。残りは本文の読解を要するものにする
   - 図（構成図・シーケンス・画面）は、表・コードブロック・Mermaid の文字で表現する。絵でないと伝わらない図だけ SVG を書いて `questions/assets/<id>-図<n>.svg` に保存し（フォルダがなければ作る）、本文から `![図n](assets/<id>-図<n>.svg)` で参照する。図に書いた情報は、設問の根拠として本文にも書くか、SVG の中の文字で読み取れるようにする
   - `templates/rubric.md` の形で `rubrics/<id>.md` に保存する（配点合計100点）

3. **問題文を保存**
   - `templates/question.md` の形で `questions/<id>.md` に保存する（`verified: false`）
   - front matter の `tags` は主タグを先頭に書く（成績集計は先頭を主タグとして扱う）

4. **解答可能性の検証**
   - `verifier` エージェントに `questions/<id>.md` のパスだけを渡す（rubric は渡さない）
   - 返ってきた解答を rubric と照らし、次のどれかに当たる設問を直す：
     - 本文から答えが一つに定まらない
     - verifier が rubric と別の妥当な解答を出した（別解として rubric に追記するか、本文を補う）
     - 設問文が曖昧
   - 修正したら `verified: true` にする。直しきれない設問は削って配点を振り直す

5. **学習者に渡す**
   - `templates/answer.md` を元に `answers/<id>.md` を作り、`started_at` に現在時刻、設問の見出しを問題に合わせて入れる
   - 問題文（`questions/<id>.md` の本文と設問）を表示する。rubric の内容や verifier の結果には触れない
   - 最後に一行で案内する：「`answers/<id>.md` に書くか、このチャットに貼ってください。終わったら `/grade`」

## 品質の目安

- 本文は profile の字数に合わせる。短すぎると読解力が鍛えられない
- ログ・設定・通信シーケンスなど、実務に近い素材を1つ以上入れる
- 字数制限は「要素2〜3個を書けばちょうど収まる」長さにする
