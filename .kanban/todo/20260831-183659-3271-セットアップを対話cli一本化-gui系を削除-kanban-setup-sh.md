---
id: 20260831-183659-3271
title: セットアップを対話CLI一本化 (GUI系を削除、kanban-setup.sh へ)
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T18:36:59
---

## Task

## 目的
セットアップ手段を gui/setup_cli.py (対話 CLI) 一本に統一する。

## 変更内容
1. 削除: gui/setup_gui.py と gui/setup_osa.py (git rm)
2. kanban-gui.sh を kanban-setup.sh にリネーム (git mv) し、中身を簡素化:
   bash 3.2 / set -euo pipefail / python3 が無ければエラー終了 / exec python3 "$REPO/gui/setup_cli.py"
   (tkinter・osascript の分岐は削除)。実行権を維持
3. gui/setup_cli.py は変更しない (現状で完結している: 状態サマリ + y/u/N の1回質問、非TTYはサマリのみ)
4. README.md の「## GUI」節を「## Setup」節の内容に合流させるか「## Setup Wizard」に改題して書き換え (英語 3〜5 行):
   - Clone, then run ./kanban-setup.sh — an interactive CLI wizard shows the environment status and
     asks once: y = install (CLI symlink + Claude Code skill, idempotent), u = uninstall (removes only
     what this installer created), N = do nothing
   - Requirements: bash + python3 (same as kanban.sh itself)
   - 旧 kanban-gui.sh / tkinter / osascript / ONE setup screen への言及を README 全体から消す

## 完了条件・検証
- gui/ に setup_core.py と setup_cli.py (と VERIFY.md) のみ。kanban-gui.sh が存在せず kanban-setup.sh が実行可能
- bash -n kanban-setup.sh、python3 -m py_compile gui/setup_core.py gui/setup_cli.py
- python3 gui/setup_cli.py </dev/null が exit 0
- grep -q kanban-setup.sh README.md が真、grep -qi tkinter README.md と grep -qi osascript README.md が偽

## History
