# 記述式問題トレーナー（Claude Code 用）

Claude Code で記述式問題の「出題 → 解答 → 採点 → 弱点更新」を回す、学習用の雛形リポジトリです。
試験ごとに `exam/profile.md` を差し替えれば、どの資格にも使えます。サンプルとして情報処理安全確保支援士（科目B）の設定を同梱しています。

必要なのは [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) と Python 3（成績集計用、標準ライブラリのみ）だけです。サーバーやアカウント登録は要りません。

## しくみ

```
/question ──▶ 採点基準を先に作って保存 ──▶ verifier が解けるか検証 ──▶ 問題を表示
                                                                      │
/grade  ◀──── 解答（ファイル or チャットに貼る） ◀────────────────────┘
   │
   ├─ grader が採点基準どおりに採点
   ├─ results/ に講評、log/history.jsonl に成績を記録
   └─ 弱点から次の分野を提案 ──▶「続けて」で /question に戻る
```

- **採点基準は出題時に作って保存**するので、解答を見てから甘くなることがありません
- **出題者・検証者・採点者を分けています**（`.claude/agents/`）。verifier は採点基準を見ずに解いて、問題の曖昧さを潰します
- **ミスを3種類に分けて記録**します：`knowledge`（知らなかった）/ `reading`（本文を読み落とした）/ `writing`（書き方で落とした）
- **択一ドリル**（`/drill`）で、午前・科目Aのような選択式の知識問題もまとめて回せます。記録は記述式と分けて集計します
- **図や写真も扱えます**。問題の図は文字（表・Mermaid）か画像で、解答は手書きの写真やスクリーンショットでも採点できます

## はじめ方

1. このリポジトリをクローン（またはテンプレートとしてコピー）して、そのフォルダで Claude Code を起動する（`claude`）
   - 学習記録も別の端末と同期したいなら、GitHub の「Use this template」で **private** リポジトリとして作るのがおすすめです（後述）
2. `/setup-exam` を実行して質問に答える（`exam/profile.md` が作られる）
   - 支援士なら `examples/sc/profile.md` をもとに、受験時期と学習者メモを聞かれるだけで終わります
3. `/question` で1問目

## 何が push されるか

このリポジトリは**雛形だけを共有**する前提です。次のものは `.gitignore` で追跡対象外なので、使い込んでも push されません。

- `exam/`：自分の試験プロファイル（学習者メモを含む）
- `questions/` `rubrics/` `answers/` `results/` `log/`：学習の記録
- `refs/` と `past-*`：公式過去問など著作物の転記

フォルダ構造を保つための `.gitkeep` と、`refs/README.md`（置く資料の説明）だけが追跡されています。別の端末でも学習を続けたい場合は、このリポジトリをテンプレートとして **private** リポジトリを作り、`.gitignore` の該当行を外して使ってください。`refs/` と `past-*` は公式資料の転記を含むので、private でも外さないことをおすすめします。

## コマンド

| コマンド | やること |
|---|---|
| `/question [タグ]` | 記述式を1問出題。タグ省略時は弱点から自動選択 |
| `/drill [タグ] [問題数]` | 択一をまとめて出題し、その場で答え合わせ（`1ア 2ウ …` で回答） |
| `/grade [id]` | 採点・講評・記録・次の提案 |
| `/review` | 分野別の成績、ミスの傾向、次の3問 |
| `/setup-exam` | 試験の設定・切り替え |
| `/import-past <年度> <問>` | `refs/` の公式過去問を取り込み、公式解答例で採点できるようにする |

解答は `answers/<id>.md` に書いても、チャットに直接貼っても大丈夫です。スマホから使うときはチャットに貼る方が楽です。
紙に手書きした解答は、写真をチャットに貼るか `answers/<id>-1.jpg` のように置けば、文字に書き起こしてから採点します（読めない字だけ確認されます）。

## 図のある問題

- 生成問題の図（構成図・シーケンス・画面）は、まず表・コードブロック・Mermaid で表現します。絵でないと伝わらない図だけ SVG にして `questions/assets/` に置きます
- 公式過去問を `/import-past` で取り込むとき、`pdftoppm`（poppler）が入っていれば図のページを画像にして `questions/assets/` に置きます。なければページ番号で参照します
  - macOS：`brew install poppler`、Ubuntu：`sudo apt install poppler-utils`

## 出題分野の選び方

`scripts/stats.py --next` が次のルールで推薦します。

- まだ一度も出ていない分野は、重みの2倍の優先度にする（最初は全分野を一巡しやすい。ただし重みの差が大きいと既出の分野が先になることもある）
- その後は「直近3回の平均点が低い × 分野の重み」が大きい順
- 直前と同じ分野は優先度を下げる

重みは `exam/profile.md` の分野タグ表で調整できます。択一ドリルも同じルールで分野を選びます（`--log drill`。直近は10問で見ます）。

## 公式過去問との併用（おすすめ）

生成問題より、公式の過去問と解答例で採点する方が精度は上です。

1. IPA などから問題冊子・解答例・採点講評をダウンロードして `refs/` に置く
2. `/import-past r06a q1` のように取り込む
3. `/question past-r06a-q1` で解く

過去問を一巡したら、生成問題で量を稼ぐ使い方がおすすめです。
`refs/` と `past-*` のファイルは公式資料の転記を含むため、`.gitignore` でコミット対象外にしています。

## カスタマイズ

| 変えたいこと | 触るファイル |
|---|---|
| 試験・形式・分野 | `exam/profile.md`（または `/setup-exam`） |
| 採点の厳しさ | `exam/profile.md` の採点方針、`.claude/agents/grader.md` |
| 問題の作り方 | `.claude/skills/question/SKILL.md`（択一は `.claude/skills/drill/SKILL.md`） |
| 択一の選択肢の数・問題数 | `exam/profile.md` の「択一問題」 |
| 結果の書式 | `templates/` |
| 分野の推薦ロジック | `scripts/stats.py` |

新しい試験のプロファイルを作ったら、`examples/<略称>/profile.md` として残しておくと使い回せます。

## ディレクトリ

```
.
├ CLAUDE.md               # Claude への全体ルール
├ .claude/
│  ├ skills/              # question / drill / grade / review / setup-exam / import-past
│  └ agents/              # verifier（検証役）/ grader（採点役）
├ exam/profile.md         # 今の試験の設定（git管理外、/setup-exam で作成）
├ examples/               # 試験ごとの設定サンプル
├ templates/              # プロファイル・問題・採点基準・解答・結果・ログのひな形
├ scripts/stats.py        # 成績集計と出題分野の推薦（Python標準ライブラリのみ）
├ questions/ rubrics/ answers/ results/
├ questions/assets/       # 問題の図（画像・SVG）
├ log/history.jsonl       # 記述式の成績
├ log/drill.jsonl         # 択一ドリルの成績（1問1行）
└ refs/                   # 公式資料（git管理外）
```

