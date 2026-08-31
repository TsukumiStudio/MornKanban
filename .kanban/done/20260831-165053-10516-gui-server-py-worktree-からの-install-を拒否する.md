---
id: 20260831-165053-10516
title: gui/server.py: worktree からの install を拒否するガード
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
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

### 2026-08-31 16:51:47 worker output (tail)

      5 - 検証
      6   - `python3 -m py_compile gui/server.py` → OK
      7   - `MORNKANBAN_GUI_PORT=8806` でサーバー起動 → `POST /api/install/cli`, `POST
      8   - `~/.local/bin/kanban` は未変更
      9 - コミット済み (`gui: worktree からの install エンドポイント実行を拒否`)
     … +3 lines

⏺ 完了

  - gui/server.py の api_install_cli() / api_install_skill() 冒頭に guard_not_worktree()
    を追加
    - REPO の絶対パスに /.kanban/wt/ が含まれる場合、400 で {"ok": false, "error": "refusing
      to install from a kanban worktree; run the GUI from the real checkout"} を返す
  - 検証
    - python3 -m py_compile gui/server.py → OK
    - MORNKANBAN_GUI_PORT=8806 でサーバー起動 → POST /api/install/cli, POST /api/install/skill
      ともに 400 + 上記 error を確認 → サーバー kill 済み
    - ~/.local/bin/kanban は未変更
  - コミット済み (gui: worktree からの install エンドポイント実行を拒否)

✻ Worked for 37s · done 4:51 PM
                                                                          ◐ medium · /effort
──────────────────────────────────────────────────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────────────────────────────────────────────────
  20260831-165053-10516  kanban/20260831-165053-10516  Sonnet 5                          /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
## 完了

- `gui/server.py` の `api_install_cli()` / `api_install_skill()` 冒頭に `guard_not_worktree()` を追加
  - REPO の絶対パスに `/.kanban/wt/` が含まれる場合、400 で `{"ok": false, "error": "refusing to install from a kanban worktree; run the GUI from the real checkout"}` を返す
- 検証
  - `python3 -m py_compile gui/server.py` → OK
  - `MORNKANBAN_GUI_PORT=8806` でサーバー起動 → `POST /api/install/cli`, `POST /api/install/skill` ともに 400 + 上記 error を確認 → サーバー kill 済み
  - `~/.local/bin/kanban` は未変更
- コミット済み (`gui: worktree からの install エンドポイント実行を拒否`)

### 2026-08-31 16:52:22 review

score: 95 / threshold: 80

実装・検証とも妥当。guard_not_worktree() が api_install_cli()/api_install_skill() 冒頭に追加され、REPO (絶対パス) に /.kanban/wt/ を含む場合に400 + 指定エラーメッセージを返すことを実際にサーバー起動・curlで再確認済み (MORNKANBAN_GUI_PORT=8807で再現)。差分は+9行のみで他挙動への影響なし。py_compile も通る。軽微な指摘: guard_not_worktree() 自体のテストは無いが規模的に許容範囲。
