---
id: 20260831-174105-15997
title: gui/setup_core.py 抽出と setup_gui.py の core 利用化
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T17:41:05
---

## Task

## 目的
gui/setup_gui.py からロジック関数を gui/setup_core.py へ抽出し、UI フロントを差し替え可能にする。
tkinter が無い環境でもロジックが import できるようにするのが狙い。

## 変更内容
1. 新規 gui/setup_core.py: 現在 setup_gui.py にあるロジック関数を移す (シグネチャ・挙動は不変):
   check_deps() / cli_installed() / skill_installed() / in_worktree() / install_cli() /
   install_skill(force=False) / load_projects() / add_project(path) / init_project(path)
   および REPO・設定ファイルパスの定義。python3 標準ライブラリのみ。tkinter を import しない
2. setup_gui.py は setup_core を import してロジックを呼ぶ形に書き換え (同ディレクトリ import:
   sys.path 調整または相対でよい)。tkinter の import は main() 内で行い、失敗したら
   стандартエラーに理由を出して exit 2 (exit コード2 = フロント不可、を選択ロジックが使う)
3. UI の見た目・文言は変えない

## 完了条件・検証
- python3 -m py_compile gui/setup_core.py gui/setup_gui.py が通る
- python3 -c "import sys; sys.path.insert(0,'gui'); import setup_core; print(setup_core.check_deps())" が
  tkinter 無しの文脈でも通る (setup_core は tkinter 非依存)
- setup_core.install_cli() が worktree 内で拒否を返す

## History
