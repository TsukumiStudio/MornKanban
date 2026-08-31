---
id: 20260831-172933-6576
title: kanban-gui.sh を tkinter 起動に変更し Web GUI を削除
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
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

### 2026-08-31 17:30:48 worker output (tail)


⏺ 完了

  - kanban-gui.sh を tkinter 版 (gui/setup_gui.py) 起動のみのシンプル版に書き換え
  - gui/server.py, gui/static/ 一式を削除
  - README.md の GUI 節を tkinter 版の説明に更新
  - 検証条件はすべて PASS
  - コミット2件 (b8d604b, 409bd3b)。gui/setup_gui.py
    は別カードで作成中のため未存在(想定通り)。push はしていない。

✻ Sautéed for 39s · done 5:30 PM

──────────────────────────────────────────────────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────────────────────────────────────────────────
  20260831-172933-6576  kanban/20260831-172933-6576  Sonnet 5                            /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
## 完了

- `kanban-gui.sh`: python3 確認 → `exec python3 "$REPO/gui/setup_gui.py"` のみのシンプル版に書き換え (ポート/open/HERDR_ENV 警告を削除、bash 3.2 互換、set -euo pipefail)。gui/setup_gui.py 自体は別カードで作成中のため未存在。
- `gui/server.py`, `gui/static/`(index.html, app.js, style.css) を `git rm` で削除
- `README.md` の `## GUI` 節を tkinter 版の説明に書き換え (他の節は無変更)

## 検証結果

- `bash -n kanban-gui.sh` → OK
- `test -x kanban-gui.sh` → OK
- `gui/server.py`, `gui/static` 不在 → OK
- `grep -q tkinter README.md` → OK
- GUI節にport等の旧記述なし → OK

## コミット

- b8d604b kanban-gui.sh: tkinter版 (gui/setup_gui.py) を起動するだけに簡素化 (kanban-gui.sh 書き換え + gui/server.py, gui/static 削除が1コミットにまとまった)
- 409bd3b README: GUI節をtkinter版の説明に更新

## 備考

- `gui/VERIFY.md` (ブラウザ版の検証記録) はタスク範囲外のため未変更で残置
- push はしていない

### 2026-08-31 17:31:14 review

score: 95 / threshold: 80

検証条件はすべて実際にPASSしている。kanban-gui.sh はpython3チェック→exec python3 gui/setup_gui.pyのみのシンプル版(bash -n OK, 実行権限OK)。gui/server.pyとgui/static/一式は削除済み。README GUI節はtkinter版の3行に書き換え済みで、port等の旧記述は残っていない。他のREADME節は無変更。gui/setup_gui.py自体は別カードで作成中のため未存在なのは想定通り。減点理由: タスク仕様は「1. kanban-gui.sh書き換え」「2. git rm gui/server.py と gui/static/」を別項目として記載していたが、実際には1コミット(b8d604b)にまとめられている。機能的には問題ないが、指示への厳密な準拠という点でわずかに逸脱。
