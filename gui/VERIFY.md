# セットアップ検証

`setup_core.py` は CLI シンボリックリンクと Claude Code / Codex の
`kanban-dispatch` スキルを導入する。スキルの正本は
`skills/kanban-dispatch/` で、インストール時に MornKanban checkout の絶対パスと
`VERSION` の内容を埋め込む。バージョン管理は `setup_core.py` の `local_version()` /
`fetch_latest_version()` / `compare_versions()` / `run_update()` が担う。

## バージョンポリシー

- 配布バージョンの正本はリポジトリ直下の `VERSION` (`X.Y.Z`、セマンティック)。
- 「最新公開バージョン」は GitHub main 上の raw `VERSION` を指す。タグ・GitHub Releases
  が無いための代替。`KANBAN_VERSION_URL` (`file://` 可) で参照先を差し替えられる
  — ネットワーク無しのテストはこれを使う。
- `kanban --version` はローカルの `VERSION` を読むだけでネットワークに触れない。
- `kanban version` は current / latest / state (`up-to-date` / `update-available` /
  `local-ahead` / `unknown`) を表示する。
- `kanban update` は dirty / detached HEAD / `main` 以外のブランチを拒否し、
  ユーザーの変更を勝手に破棄・stash しない。`git pull --ff-only origin main` の後、
  更新後のインストーラを再読み込みして CLI とスキルを再導入する。

## 自動検証

```sh
bash -n kanban.sh kanban-setup.sh kanban-secretary.sh herdr-agent-worker.sh herdr-notify-secretary.sh
python3 -m py_compile gui/setup_core.py gui/setup_cli.py monitor/*.py tests/test_kanban_secretary.py tests/test_monitor.py
python3 -m unittest -v tests/test_kanban_secretary.py tests/test_monitor.py
node --check monitor/static/app.js monitor/static/state.js
node --test tests/test_monitor_state.js
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/kanban-dispatch
```

### テストの段階 (fast / full)

`tests/test_kanban_secretary.py` の `DispatcherWorkflowTests` のうち実際に
`kanban.sh run --once` を1回通しで走らせる6本 (`test_no_conflict_merge_still_works`,
`test_merge_conflict_after_review_goes_to_resolver_then_done`,
`test_resolver_retries_on_low_review_score_then_passes`,
`test_resolve_max_attempts_exceeded_moves_to_failed_with_history`,
`test_resolve_cmd_receives_card_routing_and_conflict_context`,
`test_resolving_orphan_is_reclaimed_and_not_double_processed`) は、実 git
worktree/branch/merge を毎回作り直すため1本あたり約2秒かかり、全体の実行時間の
過半を占める (計測: 変更前 python テスト合計 中央値 36.1s → 上記6本を除いた
fast tier で 10.8s、full tier (全件) で 24.2s。3回計測の中央値、同一 machine)。

- **fast**: `KANBAN_TEST_TIER=fast python3 -m unittest tests/test_kanban_secretary.py tests/test_monitor.py tests/test_registry.py tests/test_secretary_guard.py` +
  `node --test tests/test_monitor_state.js` + 構文チェック一式。ワーカーの通常
  iteration/rework で使う。上記6本は `unittest.skipIf` でスキップされ
  (`skipped=6` と表示される)、それ以外の147本 (git worktree を伴わない
  dispatcher/resolver/monitor/registry/guard 単体ロジック) は毎回実行する。
- **full**: `KANBAN_TEST_TIER` を設定しない (未設定がデフォルト)。上記6本を含む
  全153本 + node テスト + skill validation を実行する。**マージ前の最終gate
  として必ず1回はfullを実行する。** `review_enabled: false` のプロジェクトでも、
  reviewerをfullゲート目的だけに起動するのではなく、ワーカー自身か専用の
  マージ前ステップがfullを実行してから完了とする。
- fastで緑になったことは「回帰なし」の証明にはならない。上記6本が担保する
  git worktree/merge/conflict/resolver/retry の実結合動作はfullでしか検証されない。

コマンドがcardに明記されている場合はそちらを優先する。特に指定が無い通常の
worker rework 反復では fast、マージ直前には full を使う。

テスト対象:

- 秘書 bootstrap が `.kanban/KANBAN.md` を初期化し、current Herdr agent を `secretary` として登録する
- visible dispatcher が worker / reviewer / notify を一組で別 Herdr pane に渡す
- Herdr 外では headless worker へフォールバックせず失敗する
- 同じスキルが Claude Code と Codex の両方へ導入され、checkout の実パスとバージョンが埋め込まれる
- セマンティックバージョン比較 (`1.9.0` < `1.10.0` など、辞書順にならない)
- `kanban.sh` がシンボリックリンク経由でも実体の `VERSION` / `gui/` を解決する
- `kanban.sh install` / `uninstall` が `~/.local/bin/kanban` とスキルだけを導入・削除し、
  リポジトリ本体とプロジェクトのボードは残す
- 実際の一時 git リモート + clone に対する `kanban.sh update` の fast-forward、
  dirty checkout の拒否
- `kanban-setup.sh` が引数を `gui/setup_cli.py` へ確実に転送する
  (転送漏れは対話ウィザードへ黙って落ちる既知の失敗モード)

セットアップ画面の非TTY経路は次で確認する。状態表示後、変更せず終了する。

```sh
python3 gui/setup_cli.py </dev/null
```

`install` / `update` / `uninstall` / `version` は非対話でも直接呼べる:

```sh
python3 gui/setup_cli.py version
./kanban-setup.sh install
```
