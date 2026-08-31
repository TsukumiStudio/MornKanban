---
id: 20260831-172933-6576
title: kanban-gui.sh を tkinter 起動に変更し Web GUI を削除
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T17:29:33
---

## Task

## 目的
GUI をブラウザ版から tkinter 版 (gui/setup_gui.py — 別カードが同時に作成中) へ切り替える。

## 変更内容
1. kanban-gui.sh を書き換え: python3 の存在確認 → `exec python3 "$REPO/gui/setup_gui.py"` を起動するだけの
   シンプルな内容にする (ポート・open・HERDR_ENV 警告は削除。bash 3.2 互換、set -euo pipefail)
2. git rm gui/server.py と gui/static/ 一式 (index.html, app.js, style.css) を削除
3. README.md の「## GUI」節を書き換え (他の節は触らない):
   - ./kanban-gui.sh で小さなネイティブウィンドウ (python3 標準の tkinter、追加インストール不要) が開く
   - ボタンで CLI インストール / Claude Code スキル導入 / プロジェクト登録 + kanban init
   - 日常運用は GUI ではなく秘書へ一言 (Secretary Bootstrap 参照)
   の 4〜6 行 (英語)

## 完了条件・検証
- bash -n kanban-gui.sh が通る、test -x kanban-gui.sh が真
- gui/server.py と gui/static/ が存在しない
- grep -q tkinter README.md が真、GUI 節に setup wizard の旧記述 (port 等) が残っていない

## History
