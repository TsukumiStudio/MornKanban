# セットアップ検証

`setup_core.py` は CLI シンボリックリンクと Claude Code / Codex の
`kanban-dispatch` スキルを導入する。スキルの正本は
`skills/kanban-dispatch/` で、インストール時に MornKanban checkout の絶対パスを埋め込む。

## 自動検証

```sh
python3 -m py_compile gui/setup_core.py gui/setup_cli.py tests/test_kanban_secretary.py
python3 -m unittest -v tests/test_kanban_secretary.py
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/kanban-dispatch
```

テスト対象:

- 秘書 bootstrap が `.kanban/KANBAN.md` を初期化し、current Herdr agent を `secretary` として登録する
- visible dispatcher が worker / reviewer / notify を一組で別 Herdr pane に渡す
- Herdr 外では headless worker へフォールバックせず失敗する
- 同じスキルが Claude Code と Codex の両方へ導入され、checkout の実パスが埋め込まれる

セットアップ画面の非TTY経路は次で確認する。状態表示後、変更せず終了する。

```sh
python3 gui/setup_cli.py </dev/null
```
