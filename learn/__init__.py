"""learn の部品。scripts/ と webui/ から使う。標準ライブラリのみ。

- core：ファイルと記録の読み込み（パス、front matter、profile、log/*.jsonl）
- model：学習者モデルの式
- stats・analyze・route：成績の集計、分析、出題の選択（入口は scripts/ の同名のファイル）

依存の向きは webui → stats・analyze・route → model・core。core と model はほかのモジュールを読まない。
"""
