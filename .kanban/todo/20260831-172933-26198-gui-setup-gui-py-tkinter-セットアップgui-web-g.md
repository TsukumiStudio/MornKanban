---
id: 20260831-172933-26198
title: gui/setup_gui.py: tkinter セットアップGUI (Web GUI置き換え)
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T17:29:33
---

## Task

## 目的
ブラウザ不要のネイティブポップアップ `gui/setup_gui.py` を新規作成する。python3 標準ライブラリのみ (tkinter 使用、pip 禁止)。
既存の gui/server.py の機能をローカル関数として移植する (HTTP サーバは使わない)。既存ファイルの削除は別カードが行う。

## 構成要件
- 単一ファイル。ロジック関数と UI を分離し、`if __name__ == "__main__": main()` で起動
  (import しただけでは何も起きない — 別カードの検証がヘッドレスで import する)
- REPO = このファイルの2階層上の絶対パス
- 設定 ~/.config/mornkanban/gui.json = {"projects":[...]} (Web 版と互換)

## ロジック関数 (モジュートップレベル、UI 非依存でテスト可能に)
- check_deps() -> dict: {"herdr":bool,"claude":bool,"codex":bool} (shutil.which)
- cli_installed() -> bool: which("kanban") または ~/.local/bin/kanban が存在
- skill_installed() -> bool: ~/.claude/skills/kanban-dispatch/SKILL.md が存在
- in_worktree() -> bool: REPO のパスに "/.kanban/wt/" を含む
- install_cli() -> (ok:bool, msg:str): in_worktree() なら拒否。~/.local/bin を作成し kanban → REPO/kanban.sh の
  symlink を張り替え (既存が通常ファイルなら拒否)。~/.local/bin が PATH に無ければ msg で警告
- install_skill(force=False) -> (ok:bool, msg:str): in_worktree() なら拒否。既存かつ not force なら
  (False, "already installed")。内容は name/description/user_invocable の frontmatter と
  「REPO/README.md を読んで従う」本文 (Web 版と同じ文面)
- load_projects() -> list[dict{path,name,has_kanban}] / add_project(path) -> (ok,msg) / 
  init_project(path) -> (ok,msg): ["bash", REPO/kanban.sh, "init", path] を subprocess.run(timeout=30)

## UI (main)
- ウィンドウタイトル "MornKanban Setup"、最小 520x480
- 上から: 依存表示 (herdr/claude/codex を ✓/✗ とともに1行)、
  Step1: 状態ラベル (導入済み/未導入) + [kanban CLI をインストール] ボタン、
  Step2: 状態ラベル + [Claude Code スキルを導入] ボタン (already installed なら messagebox.askyesno で上書き確認→force)、
  Step3: パス入力 Entry + [追加] + プロジェクト Listbox + [選択を kanban init]、
  下部: 「導入後は Herdr のペインで claude を起動し『kanban の秘書として待機して』と一言」の案内ラベル
- 各操作後に状態を再描画。エラーは messagebox.showerror。tkinter import 失敗時は
  標準エラーに「python3 の tkinter が必要」と出して exit 1 (main 内で)

## 完了条件・検証
- python3 -m py_compile gui/setup_gui.py が通る
- DISPLAY の無い文脈でも python3 -c "import sys; sys.path.insert(0,'gui'); import setup_gui; print(setup_gui.check_deps(), setup_gui.cli_installed(), setup_gui.skill_installed(), setup_gui.in_worktree())" が例外なく表示されること (worktree 内なので in_worktree は True になるはず)
- install 系はこの検証では呼ばない (worktree ガードの単体確認として setup_gui.install_cli() が拒否を返すことだけ確認してよい)

## History
