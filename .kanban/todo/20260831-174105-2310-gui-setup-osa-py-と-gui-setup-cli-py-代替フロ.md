---
id: 20260831-174105-2310
title: gui/setup_osa.py と gui/setup_cli.py: 代替フロント2種
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T17:41:05
---

## Task

## 目的
tkinter が無い環境向けのフロントを2つ新規作成する。ロジックは gui/setup_core.py (別カードが同時作成中) を
import して使う。core の関数契約:
check_deps()->dict / cli_installed()->bool / skill_installed()->bool / in_worktree()->bool /
install_cli()->(ok,msg) / install_skill(force=False)->(ok,msg: 既存かつ not force なら (False,"already installed")) /
load_projects()->list[{path,name,has_kanban}] / add_project(path)->(ok,msg) / init_project(path)->(ok,msg)

## 1. gui/setup_osa.py (macOS ネイティブポップアップ)
- osascript を subprocess で呼ぶ。ループ: choose from list で
  ["CLI をインストール (状態: 導入済み/未導入)", "Claude Code スキルを導入 (状態: …)", "プロジェクトを追加して init", "プロジェクト一覧", "終了"]
- スキル導入で already installed の場合は display dialog (2ボタン) で上書き確認 → force
- プロジェクト追加は display dialog default answer "" でパス入力 → add_project → init するか確認 → init_project
- 結果は display dialog (OK のみ) で表示。キャンセル (osascript 終了コード1) は静かにメニューへ戻る
- 冒頭に deps を表示 (display dialog)。macOS 以外や osascript 不在なら stderr に出して exit 2

## 2. gui/setup_cli.py (どこでも動く対話ウィザード)
- input() の番号メニューで同じ5操作。EOF / q で終了 (exit 0)
- 非TTY (stdin が閉じている等) では状態サマリ (deps / cli / skill / projects) を print して exit 0
  — 自動検証がここを通る

## 完了条件・検証
- python3 -m py_compile gui/setup_osa.py gui/setup_cli.py が通る
- echo q | python3 gui/setup_cli.py が exit 0 で状態サマリまたはメニューを出す
- python3 gui/setup_cli.py </dev/null が exit 0
- osascript のダイアログはこの検証では開かない (macOS 判定と exit 2 経路のみ確認)

## History
