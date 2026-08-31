# gui/setup_gui.py 検証 (tkinter 版, v6)

UI を開かず (mainloop 未実行)、ロジック関数を直接呼び出して検証した。

## 1. 構成
- `gui/setup_gui.py` が存在する: PASS
- `gui/server.py`, `gui/static/` は存在しない (`ls gui/` → `VERIFY.md`, `setup_gui.py` のみ): PASS
- `bash -n kanban-gui.sh`: PASS (構文エラーなし)
- `kanban-gui.sh` が `setup_gui.py` を起動: PASS
  - `grep -n "setup_gui.py" kanban-gui.sh` → `11:exec python3 "$REPO/gui/setup_gui.py"`

## 2. py_compile
- `python3 -m py_compile gui/setup_gui.py`: PASS (エラーなし)

## 3. import テスト
- `python3 -c "import sys; sys.path.insert(0,'gui'); import setup_gui"`: PASS
  - `main()` は呼ばれないため import 時点で tkinter の `Tk()` / mainloop は実行されない (副作用なし)

## 4. ロジック関数 (UI 非依存)
- `check_deps()` → `{'herdr': True, 'claude': True, 'codex': True}`: PASS (bool dict を返す)
- `cli_installed()` → `True`: PASS (bool を返す)
- `skill_installed()` → `True`: PASS (bool を返す)
- `in_worktree()` → `True` (REPO = `/Users/matsufriends/git/MornKanban/.kanban/wt/20260831-173139-14900`, `/.kanban/wt/` を含む): PASS
- `install_cli()` → `(False, "refusing to install from a kanban worktree; run from the real checkout")`: PASS (worktree ガードで拒否)
  - `~/.local/bin/kanban` のリンク先: 検証前後とも `/Users/matsufriends/git/MornKanban/kanban.sh` で不変: PASS
- `install_skill()` → `(False, "refusing to install from a kanban worktree; run from the real checkout")`: PASS (同ガードで拒否)
- プロジェクト操作:
  - `mkdir -p /tmp/kanban-gui-verify6`
  - `add_project("/tmp/kanban-gui-verify6")` → `(True, "added: /tmp/kanban-gui-verify6")`: PASS
  - `init_project("/tmp/kanban-gui-verify6")` → `(True, "initialized: /tmp/kanban-gui-verify6")`: PASS
  - `/tmp/kanban-gui-verify6/.kanban` が生成されている: PASS
  - `load_projects()` に `{'path': '/tmp/kanban-gui-verify6', 'name': 'kanban-gui-verify6', 'has_kanban': True}` として掲載: PASS

## 5. README
- `grep -q tkinter README.md`: PASS
  - `README.md:13`: "...a small native window opens (Python's standard `tkinter`, no extra install needed)."

## 総合判定
**PASS** — 全項目クリア。tkinter 版 GUI のロジックは非 UI 経路で正常動作を確認。
`~/.local/bin/kanban` のリンク先 (`/Users/matsufriends/git/MornKanban/kanban.sh`) は検証前後で変化なし。
