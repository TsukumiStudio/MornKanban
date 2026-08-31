---
id: 20260831-183659-3271
title: セットアップを対話CLI一本化 (GUI系を削除、kanban-setup.sh へ)
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
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

### 2026-08-31 18:38:58 worker output (tail)

  0、README のgrep条件)。

  スコープ外: .kanban/KANBAN.md
  に旧kanban-gui.shへの言及が残っている(指示範囲はREADME.mdのみだったため未変更)。

  回答は .kanban-answer.md にも書き込み済み。

✻ Crunched for 1m 44s · done 6:38 PM

──────────────────────────────────────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────────────────────────────────────
  20260831-183659-3271  kanban/20260831-183659-3271  Sonnet 5                /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
## 完了

セットアップ手段を gui/setup_cli.py (対話 CLI) 一本に統一した。

### 変更内容
- `git rm gui/setup_gui.py gui/setup_osa.py`
- `git mv kanban-gui.sh kanban-setup.sh`。中身を簡素化: bash 3.2 / `set -euo pipefail` / python3 不在時にエラー終了 / `exec python3 "$REPO/gui/setup_cli.py"`。tkinter・osascript 分岐を削除。実行権 (`chmod +x`) 維持
- `gui/setup_cli.py` は無変更
- README.md の「## GUI」節を「## Setup Wizard」に改題し、y/u/N の対話フローと bash+python3 要件のみを記載。tkinter/osascript/kanban-gui.sh/ONE setup screen への言及を除去

### コミット (細分)
- `5f1789b` kanban: GUI/OSAセットアップスクリプトを削除
- `b5210e9` kanban: README の Setup 節を対話CLI一本化に合わせて更新
- `3b9f1b8` kanban: kanban-setup.sh の tkinter/osascript 分岐を削除し対話CLI一本化

### 検証結果 (全てPASS)
- `ls gui/` → `VERIFY.md setup_cli.py setup_core.py` のみ
- `kanban-gui.sh` は存在せず、`kanban-setup.sh` は実行可能 (`-rwxr-xr-x`)
- `bash -n kanban-setup.sh` → OK
- `python3 -m py_compile gui/setup_core.py gui/setup_cli.py` → OK
- `python3 gui/setup_cli.py </dev/null` → exit 0
- `grep -q kanban-setup.sh README.md` → 真
- `grep -qi tkinter README.md` → 偽 / `grep -qi osascript README.md` → 偽

### スコープ外として変更しなかったもの
- `.kanban/KANBAN.md` に `kanban-gui.sh` への言及が残る (プロジェクト概要の記述、指示範囲は README.md のみだったため未変更。必要なら別途更新を依頼してほしい)

### 2026-08-31 18:39:28 review

score: 92 / threshold: 80

全ての完了条件・検証コマンドが実際にPASSすることを確認した (bash -n, py_compile, setup_cli.py exit 0, README grep条件全て一致)。git mv によるリネームも検出されている (kanban-gui.sh => kanban-setup.sh)。gui/ には setup_core.py, setup_cli.py, VERIFY.md のみが残る。減点理由: .kanban/KANBAN.md:19 に旧 kanban-gui.sh への言及が残存 (worker は指示範囲外として意図的にスコープ外にしたと申告しているが、ユーザー向けドキュメントの一貫性としては軽微な減点対象)。
