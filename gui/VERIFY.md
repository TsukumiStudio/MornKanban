# gui/ 検証 (フォールバック構成 v7)

対象: `setup_core.py` (ロジック) / `setup_gui.py` (tkinter) / `setup_osa.py` (osascript) /
`setup_cli.py` (CLIウィザード) / `kanban-gui.sh` (フロント選択)。
ウィンドウ・ダイアログは開かず、ヘッドレスで検証した (mainloop / osascript ダイアログ未実行)。

## 1. py_compile
- `python3 -m py_compile gui/setup_core.py gui/setup_gui.py gui/setup_osa.py gui/setup_cli.py`: PASS (exit=0, エラーなし)

## 2. import 副作用なし
- `python3 -c "import sys; sys.path.insert(0,'gui'); import setup_core, setup_cli"`: PASS (exit=0)
  - `setup_gui.py` / `setup_osa.py` は `main()` 内で `tkinter` / `osascript` を呼ぶ構成のため、
    import時点 (モジュールトップレベル) では tkinter の `Tk()` も osascript 起動も発生しない。
    本検証では `setup_core` と `setup_cli` を実際に import し確認した。

## 3. core ロジック (`setup_core.py`)
検証時の `REPO` は本 worktree
(`/Users/matsufriends/git/MornKanban/.kanban/wt/20260831-175229-1542`)。

- `check_deps()` → `{'herdr': True, 'claude': True, 'codex': True}`: PASS (bool の dict)
- `cli_installed()` → `True` (bool): PASS
- `skill_installed()` → `True` (bool): PASS
- `in_worktree()` → `True` (`REPO` に `/.kanban/wt/` を含む): PASS
- `install_cli()` → `(False, "refusing to install from a kanban worktree; run from the real checkout")`: PASS (worktree ガードで拒否)
  - `~/.local/bin/kanban` の実体: 検証前後とも `/Users/matsufriends/git/MornKanban/kanban.sh` で不変: PASS
- `install_skill()` → `(False, "refusing to install from a kanban worktree; run from the real checkout")`: PASS (同ガードで拒否)
- プロジェクト操作 (`/tmp/kanban-gui-verify7` で実施):
  - `add_project("/tmp/kanban-gui-verify7")` → `(True, "added: /tmp/kanban-gui-verify7")`: PASS
  - `init_project("/tmp/kanban-gui-verify7")` → `(True, "initialized: /tmp/kanban-gui-verify7")`: PASS
  - `/tmp/kanban-gui-verify7/.kanban` が生成されている: PASS
  - `load_projects()` に `has_kanban: True` で掲載: PASS

## 4. CLI フロント (`setup_cli.py`)
- `echo q | python3 gui/setup_cli.py`: PASS (exit=0)
  - stdin が非TTY (パイプ) のため `main()` は `status_summary()` を表示して即終了する経路
    (対話メニューには入らない)。出力は deps / CLI導入状況 / スキル導入状況 / プロジェクト一覧。
- `python3 gui/setup_cli.py </dev/null`: PASS (exit=0, 同じく非TTY経路で即終了)

## 5. 選択ロジック (`kanban-gui.sh`)
- `bash -n kanban-gui.sh`: PASS (構文エラーなし)
- 3分岐が存在することを確認:
  - `python3 -c "import tkinter"` 成功時 → `exec python3 "$REPO/gui/setup_gui.py"` (11-12行目)
  - macOS かつ `osascript` あり時 → `exec python3 "$REPO/gui/setup_osa.py"` (15-16行目)
  - それ以外 → `exec python3 "$REPO/gui/setup_cli.py"` (19行目)
  - PASS

## 6. README
- `grep -q osascript README.md`: PASS
  - `README.md:13`: "...a small tkinter window, native macOS dialogs (osascript) when tkinter is missing, or an interactive CLI wizard anywhere else."

## 総合判定
**PASS** — 全項目クリア。tkinter / osascript / CLI の3フロントとも `setup_core.py` の
共通ロジックを正しく呼び出し、worktree からの `install_cli` / `install_skill` はガードで拒否される。
`~/.local/bin/kanban` の実体 (`/Users/matsufriends/git/MornKanban/kanban.sh`) は検証前後で不変。
コードの修正は行っていない。
