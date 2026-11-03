# 記述式問題トレーナー（Claude Code 用）

Claude Code で記述式問題の「出題 → 解答 → 採点 → 弱点更新」を回す、学習用の雛形リポジトリです。
試験ごとに `exam/profile.md` を差し替えれば、どの資格にも使えます。サンプルとして情報処理安全確保支援士（科目B）の設定を同梱しています。

必要なのは [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) と Python 3（成績集計用、標準ライブラリのみ）だけです。サーバーやアカウント登録は要りません。ターミナルの代わりに[ブラウザから使う](#ブラウザから使うセルフホスト)こともできます。

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

## ブラウザから使う（セルフホスト）

ターミナルで操作する代わりに、ブラウザで学習の種類・分野・問題数を選んで回せます。自分の PC で動かすだけで、アカウント登録やデータベースは要りません。

- **学習する**：記述式 / 択一ドリル / 取り込んだ過去問 / 過去問の取り込み / 振り返り を選び、分野（おまかせ＝弱点から自動）と問題数を指定して「はじめる」。問題は画面に表示され、記述式は解答欄に書いて「保存して採点する」、択一は返信欄に `1ア 2ウ …` で答えます
- **成績**：`scripts/analyze.py` と同じ得意・不得意の判定を表で表示（Claude を呼ばないので無料・一瞬）
- **履歴**：出題済みの問題・解答・講評を見返す（採点基準 `rubrics/` は画面から読めません）
- **設定**：`examples/` のサンプルをコピー、または Claude と対話して試験を設定。`exam/profile.md` を画面で編集

画面は依頼を Claude Code に渡して返事を見せるだけで、出題・検証・採点はこのリポジトリのスキルがそのまま行います。記録されるファイル（`questions/` `log/` など）もターミナルで使うときと同じなので、両方を行き来できます。Claude Code への渡し方は2通りあります。

### A. PC の Claude Code に `/loop` で処理させる（既定）

```bash
python3 webui/server.py          # → http://127.0.0.1:8765/
claude                           # 別のターミナルで、このフォルダで起動して
> /loop 1m /inbox                # 1分ごとにブラウザからの依頼を処理する
```

- 画面での操作は `inbox/<依頼>/NNN-user.md` に置かれ、`/inbox` スキル（`.claude/skills/inbox/`）が処理して `NNN-claude.md` に返事を書きます。サーバー自身は Claude を呼びません
- いつもの Claude Code（サブスクリプションでも API キーでも）をそのまま使います。1つのセッションが回り続けるので、会話の流れも引き継がれます
- 放っておいても進むように、`claude --permission-mode acceptEdits` で起動するか、`python3 scripts/stats.py` `python3 scripts/analyze.py` `date` `mkdir` などを許可しておくと止まりません
- 依頼は並べて置けます。返事が来るまで、画面には「Claude Code が拾うのを待っています」と出ます

### B. サーバーが `claude -p` で処理する（API キーでも可）

常駐の Claude Code を用意しないときは、サーバーが依頼ごとに `claude -p "/drill web 5"` を起動します。

```bash
python3 webui/server.py --runner cli
ANTHROPIC_API_KEY=sk-ant-... python3 webui/server.py --runner cli   # API キー（従量課金）で動かす
```

- 認証は `claude` コマンドと同じです：ログイン済みのサブスクリプション、`CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` で発行）、`ANTHROPIC_API_KEY` のどれでも動きます
- `claude -p` は対話で許可を求められないので、スキルが使う操作（このフォルダ内のファイル編集と `scripts/` の実行など）だけを許可して動かします（`webui/server.py` の `ALLOWED_TOOLS`）。サブエージェント（verifier・grader）の出力や、採点基準を書き込む操作の中身は画面に流しません
- 同時に動かせる作業は1つだけです。問題の作成には数分かかります
- 使うモデルを変えたいときは `LEARN_CLAUDE_ARGS="--model sonnet"`

### スマホなど別の端末から使う

既定では自分の PC（127.0.0.1）からしか開けません。同じ LAN の端末から使うときはトークンを決めて起動し、`?token=` 付きの URL を一度開きます（以後は Cookie で覚えます）。

```bash
LEARN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))") python3 webui/server.py --host 0.0.0.0
# → http://<PCのIPアドレス>:8765/?token=<そのトークン>
```

トークンは英数字と `-` `_` だけにしてください（`#` や `&` を含むと URL で途中までしか送られず、開けません）。

インターネットに直接公開する使い方は想定していません。外から使いたいときは Tailscale などの VPN 越しにしてください。

## コマンド

| コマンド | やること |
|---|---|
| `/question [タグ]` | 記述式を1問出題。タグ省略時は弱点から自動選択 |
| `/drill [タグ] [問題数]` | 択一をまとめて出題し、その場で答え合わせ（`1ア 2ウ …` で回答） |
| `/grade [id]` | 採点・講評・記録・次の提案 |
| `/review` | 得意・不得意な分野とその原因、ミスの傾向、次の3問 |
| `/setup-exam` | 試験の設定・切り替え |
| `/import-past <年度> <問>` | `refs/` の公式過去問を取り込み、公式解答例で採点できるようにする |
| `/inbox` | ブラウザの画面から届いた依頼を処理する（`/loop 1m /inbox` で常駐） |

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

## 得意・不得意の分析

`/review`（または `python3 scripts/analyze.py`）で、記述式と択一の記録を合わせて分野ごとに判定します。

| 判定 | 意味 |
|---|---|
| 苦手 | 見込みが合格ライン（`exam/profile.md` の合格基準。読めなければ60%）未満 |
| ボーダー | 合格ライン〜+15pt |
| 得意 | 合格ライン+15pt以上 |
| 測定中 | 記録が少ない（記述式は3件、択一は5問に満たない。副タグとして出た記録は0.5件と数える） |

- 見込みは分野ごとに、記述式と択一を**別々に**見積もります。1回ごとに少しずつ動かす方式（Elo）なので、新しい結果ほど強く効き、記録が少ないうちは50%に寄せます
- 判定には、記録が足りている形式のうち**低い方**を使います。択一で満点でも記述式で落としていれば「苦手」になります
- 見込みには95%の幅を付け、幅が合格ラインをまたがなければ「（確か）」と表示します
- 苦手・ボーダーの分野には原因の見当を付けます：択一と記述の差（知識の問題か、読み方・書き方の問題か）、最も多いミスの種類、その分野で記録された弱点
- 分野をまたいで繰り返している弱点（同じ用語・観点）も拾います

考え方は、SaaS 版の [imohiyoko/learning](https://github.com/imohiyoko/learning) の学習者モデル（ADR 0018）を、1人・ファイルだけで回せる形に縮めたものです。概念辞書・難易度・記憶（FSRS）は扱わず、分野タグの単位で見ます。

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
| 得意・不得意の判定 | `scripts/analyze.py`（先頭の定数） |

新しい試験のプロファイルを作ったら、`examples/<略称>/profile.md` として残しておくと使い回せます。

## ディレクトリ

```
.
├ CLAUDE.md               # Claude への全体ルール
├ .claude/
│  ├ skills/              # question / drill / grade / review / setup-exam / import-past / inbox
│  └ agents/              # verifier（検証役）/ grader（採点役）
├ exam/profile.md         # 今の試験の設定（git管理外、/setup-exam で作成）
├ examples/               # 試験ごとの設定サンプル
├ templates/              # プロファイル・問題・採点基準・解答・結果・ログのひな形
├ scripts/stats.py        # 成績集計と出題分野の推薦（Python標準ライブラリのみ）
├ scripts/analyze.py      # 得意・不得意の分析（記述式と択一を合わせて）
├ webui/                  # ブラウザから使うためのローカルサーバーと画面（python3 webui/server.py）
├ inbox/                  # 画面から Claude Code への依頼（/inbox で処理。git管理外）
├ tests/                  # スクリプトのテスト（python3 -m unittest discover -s tests）
├ questions/ rubrics/ answers/ results/
├ questions/assets/       # 問題の図（画像・SVG）
├ log/history.jsonl       # 記述式の成績
├ log/drill.jsonl         # 択一ドリルの成績（1問1行）
└ refs/                   # 公式資料（git管理外）
```

