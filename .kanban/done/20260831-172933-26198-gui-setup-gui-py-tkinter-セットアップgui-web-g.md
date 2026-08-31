---
id: 20260831-172933-26198
title: gui/setup_gui.py: tkinter セットアップGUI (Web GUI置き換え)
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
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

### 2026-08-31 17:30:35 worker output (tail)

    非依存でモジュールトップレベルに配置
  - py_compile 通過、headless import で check_deps/cli_installed/skill_installed/in_worktree
    が例外なく実行 (in_worktree() は worktree 内実行のため True)
  - install_cli() の worktree ガードが拒否を返すことを確認 (install系はこれのみ呼び出し)
  - 回答は .kanban-answer.md にも書き込み済み

✻ Cooked for 49s · done 5:30 PM

──────────────────────────────────────────────────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────────────────────────────────────────────────
  20260831-172933-26198  kanban/20260831-172933-26198  Sonnet 5                          /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
gui/setup_gui.py を新規作成した。

## 検証結果
- `python3 -m py_compile gui/setup_gui.py` → OK
- `import setup_gui; check_deps(), cli_installed(), skill_installed(), in_worktree()`
  → `{'herdr': True, 'claude': True, 'codex': True} True True True`
  (worktree 内実行のため in_worktree() は True)
- `install_cli()` → `(False, 'refusing to install from a kanban worktree; run from the real checkout')` (ガード動作確認、install系はこれ以外呼んでいない)

## 実装内容
- 単一ファイル、標準ライブラリのみ (tkinter, subprocess, shutil, json, os)
- ロジック関数 (モジュールトップレベル、UI非依存):
  check_deps, cli_installed, skill_installed, in_worktree, install_cli,
  install_skill(force=False), load_projects, add_project, init_project
  (path_contains, load_config/save_config/project_entry は既存 server.py 同様の内部ヘルパー)
- UI は main() 内に閉じ、`if __name__ == "__main__": main()` でのみ起動
- tkinter import 失敗時は標準エラーに「python3 の tkinter が必要」と出して exit 1
- UI 構成: 依存表示 → Step1(CLI導入) → Step2(スキル導入、既存時は askyesno で上書き確認) →
  Step3(パス入力/追加/一覧/init) → 案内ラベル。各操作後に refresh() で再描画、エラーは showerror

### 2026-08-31 17:31:12 review

score: 96 / threshold: 80

実装は要件を満たす。単一ファイル・標準ライブラリのみ・ロジック関数がモジュールトップレベルでUI非依存・if __name__=='__main__': main() で起動を確認。check_deps/cli_installed/skill_installed/in_worktree/install_cli/install_skill/load_projects/add_project/init_project は server.py のロジックと完全一致 (skill テンプレート文面も同一)。py_compile 通過、DISPLAYなしのheadless importでcheck_deps等が例外なく実行され in_worktree()=True を確認。install_cli() のworktreeガードが拒否を返すことも確認。UI構成 (依存表示、Step1/2/3、案内ラベル、askyesno上書き確認、tkinter import失敗時のexit 1) も指示通り。減点理由: 目視でのGUI起動確認 (ボタン押下やLisbox表示の実動作) はこのレビューでは行っておらず、tkinter未インストール環境でのexit 1パスも実行未確認 (コード上は妥当と判断)。
