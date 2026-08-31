---
id: 20260831-174105-15997
title: gui/setup_core.py 抽出と setup_gui.py の core 利用化
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
created: 2026-08-31T17:41:05
---

## Task

## 目的
gui/setup_gui.py からロジック関数を gui/setup_core.py へ抽出し、UI フロントを差し替え可能にする。
tkinter が無い環境でもロジックが import できるようにするのが狙い。

## 変更内容
1. 新規 gui/setup_core.py: 現在 setup_gui.py にあるロジック関数を移す (シグネチャ・挙動は不変):
   check_deps() / cli_installed() / skill_installed() / in_worktree() / install_cli() /
   install_skill(force=False) / load_projects() / add_project(path) / init_project(path)
   および REPO・設定ファイルパスの定義。python3 標準ライブラリのみ。tkinter を import しない
2. setup_gui.py は setup_core を import してロジックを呼ぶ形に書き換え (同ディレクトリ import:
   sys.path 調整または相対でよい)。tkinter の import は main() 内で行い、失敗したら
   стандартエラーに理由を出して exit 2 (exit コード2 = フロント不可、を選択ロジックが使う)
3. UI の見た目・文言は変えない

## 完了条件・検証
- python3 -m py_compile gui/setup_core.py gui/setup_gui.py が通る
- python3 -c "import sys; sys.path.insert(0,'gui'); import setup_core; print(setup_core.check_deps())" が
  tkinter 無しの文脈でも通る (setup_core は tkinter 非依存)
- setup_core.install_cli() が worktree 内で拒否を返す

## History

### 2026-08-31 17:42:40 worker output (tail)

    import 失敗時は exit(2) (旧: 1)。UI見た目・文言は不変。

  検証: py_compile OK、tkinter無しで setup_core.check_deps()
  動作確認、worktree内で install_cli() が拒否を返すことを確認、setup_gui.py
  がヘッドレス環境で exit code 2 を返すことを確認。

  コミット eafc334 作成済み (未push)。回答は .kanban-answer.md にも記載済み。

✻ Worked for 1m 13s · done 5:42 PM

──────────────────────────────────────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────────────────────────────────────
  20260831-174105-15997  kanban/20260831-174105-15997  Sonnet 5              /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
## 結果

gui/setup_gui.py からロジック関数を gui/setup_core.py へ抽出した。

### 変更内容
- 新規 `gui/setup_core.py`: ロジック関数 (check_deps / cli_installed / skill_installed /
  in_worktree / install_cli / install_skill / load_projects / add_project / init_project)
  と、それらが依存する内部ヘルパー (path_contains / load_config / save_config / project_entry)、
  REPO・設定ファイルパス定義を移設。python3 標準ライブラリのみ、tkinter は import しない。
- `gui/setup_gui.py`: setup_core を import してロジックを呼ぶ形に書き換え。tkinter の import
  は main() 内で行い、失敗時は stderr にメッセージを出して `exit(2)` (旧: exit(1) から変更)。
  UI の見た目・文言・シグネチャは不変。

### 検証結果
- `python3 -m py_compile gui/setup_core.py gui/setup_gui.py` → OK
- `python3 -c "import sys; sys.path.insert(0,'gui'); import setup_core; print(setup_core.check_deps())"`
  → tkinter 非依存で動作確認 (`{'herdr': True, 'claude': True, 'codex': True}`)
- `setup_core.install_cli()` を worktree 内で実行 →
  `(False, 'refusing to install from a kanban worktree; run from the real checkout')` で拒否を確認
- `python3 gui/setup_gui.py` (tkinter 使用不可な文脈) → stderr にメッセージを出し exit code 2 を確認

### コミット
- `eafc334` kanban: setup_gui のロジックを setup_core へ抽出、tkinter非依存に

未push。

### 2026-08-31 17:43:19 review

score: 92 / threshold: 80

抽出は正確。setup_core.py は tkinter非依存でロジック関数・ヘルパー(path_contains/load_config/save_config/project_entry)・パス定義を全て保持しシグネチャ/挙動不変。setup_gui.py は setup_core を import する形に整理され、tkinter import は main() 内、失敗時 exit(2) (旧exit(1)から変更、指示通り)。UI見た目・文言は無変更(diff確認済み)。検証項目3つとも実機で再確認: py_compile OK、tkinter無しで check_deps() 動作、worktree内で install_cli() が拒否を返す、setup_gui.py 実行で stderr出力+exit code 2。減点理由: gui/__pycache__/setup_gui.cpython-314.pyc のバイトコード差分がコミットに含まれている(既存の追跡ファイルでタスク起因の新規問題ではないが、git rm --cached で外すのが望ましい)。
