---
id: 20260831-180513-623
title: setup_core: run_uninstall() 追加
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T18:05:13
---

## Task

## 目的
gui/setup_core.py にアンインストール機能を追加する。他ファイルは触らない。

## run_uninstall() -> list[str] の仕様 (冪等・安全側)
- in_worktree() なら ["refused: kanban worktree 内"] を返すだけ
- CLI symlink (~/.local/bin/kanban):
  - 存在しない → "CLI: 未導入"
  - symlink でリンク先の realpath が REPO 配下 → 削除して "CLI: 削除しました"
  - それ以外 (通常ファイル・他リポジトリを指す symlink) → 削除せず "CLI: このインストーラの管理物ではないため残しました"
- スキル (~/.claude/skills/kanban-dispatch/):
  - SKILL.md が無い → "スキル: 未導入"
  - SKILL.md の内容に "MornKanban" を含む (インストーラ製の目印) → ディレクトリごと削除して "スキル: 削除しました"
  - 含まない → 削除せず "スキル: 別管理のスキルのため残しました"
- 例外は握りつぶさず (False 系メッセージに変換して) リストで返す。戻り値は実施結果メッセージのリスト

## 完了条件・検証
- python3 -m py_compile gui/setup_core.py
- worktree 内での python3 -c "...; print(setup_core.run_uninstall())" が refused を返す (実削除は走らない)
- 単体ロジックの確認として、HOME を一時ディレクトリに差し替えた subprocess
  (env HOME=/tmp/uninst-test python3 -c ...) で「未導入」2件が返ることを確認 (実環境の ~/. には触らない)

## History
