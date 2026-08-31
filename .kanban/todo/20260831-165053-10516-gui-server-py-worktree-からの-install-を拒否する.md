---
id: 20260831-165053-10516
title: gui/server.py: worktree からの install を拒否するガード
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T16:50:53
---

## Task

## 目的
gui/server.py の install 系エンドポイントに小さなガードを追加する。

## 背景 (実際に起きたバグ)
検証エージェントが kanban の worktree (.kanban/wt/<id>/) 内でサーバを起動して POST /api/install/cli を叩いた結果、
~/.local/bin/kanban の symlink が worktree の kanban.sh に張り替えられ、worktree 削除後にリンク切れになった。

## 変更内容
- POST /api/install/cli と POST /api/install/skill の冒頭で、REPO の絶対パスに "/.kanban/wt/" が含まれる場合は
  {"ok":false,"error":"refusing to install from a kanban worktree; run the GUI from the real checkout"} を 400 で返す
- 他の挙動は一切変えない

## 完了条件・検証
- python3 -m py_compile gui/server.py が通る
- このカード自体が worktree で動いている点を利用: MORNKANBAN_GUI_PORT=8806 でサーバを起動し、
  curl -s -X POST http://127.0.0.1:8806/api/install/cli が 400 と上記 error を返すことを確認して kill
- ~/.local/bin/kanban には一切触らない

## History
