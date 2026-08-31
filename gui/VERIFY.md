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
過半を占める (計測: python テスト合計 中央値 23.7s → 上記6本を除いた fast
tier で 10.5s。3回計測の中央値、同一 machine、`TestTierContractTests` 追加後の
全156本ベース)。この6本を `unittest.skipIf` でスキップした fast tier の
`TestTierContractTests` (下記) が固定するメンバーシップ自体は、それとは別に
`test_full_only_membership_is_exactly_the_documented_six` /
`test_full_only_actually_skips_under_fast_tier` として毎回実行される。

- **fast**: `KANBAN_TEST_TIER=fast python3 -m unittest tests/test_kanban_secretary.py tests/test_monitor.py tests/test_registry.py tests/test_secretary_guard.py` +
  `node --test tests/test_monitor_state.js` + 構文チェック一式。ワーカーの通常
  iteration/rework で使う。上記6本は `unittest.skipIf` でスキップされ
  (`skipped=6` と表示される)、それ以外の150本 (git worktree を伴わない
  dispatcher/resolver/monitor/registry/guard 単体ロジック、fast/full 収録範囲を
  固定する `TestTierContractTests` 自身を含む) は毎回実行する。
- **full**: `KANBAN_TEST_TIER` を設定しない (未設定がデフォルト)。上記6本を含む
  全156本 + node 9件 + skill validation を実行する。**マージ前の最終gate
  として必ず1回はfullを実行する。** MornKanban の dispatcher (`kanban.sh`) には
  reviewer は `review_enabled: false` またはcardの `--no-review` で省略できる。
  reviewer OFFでも、マージ前full gateはworker自身または明示した検証ステップが
  必ず1回だけ実行する。
- fastで緑になったことは「回帰なし」の証明にはならない。上記6本が担保する
  git worktree/merge/conflict/resolver/retry の実結合動作はfullでしか検証されない。
- **173件前後という当初見積もりについて**: 実測は156本 (python) + 9本 (node) =
  165本 (この card 着手前は153+9=162本)。173という数字を裏付ける過去の
  ファイル・コミットは見つからず、見積もり時の概算誤差と判断する。棚卸しの
  結果、過去実装削除後も残る死んだtestや、同一contractを同一levelで重複検証
  しているtestは見つからなかった (クラス単位の件数は下表)。

| クラス/ファイル | 本数 | 種別 |
| --- | ---: | --- |
| `SecretaryScriptTests` | 11 | shell/CLI (bootstrap, symlink解決) |
| `SecretaryNameResolutionTests` | 13 | pure unit (エージェント名解決) |
| `NotifySecretaryRoutingTests` | 3 | shell/CLI (notify hook) |
| `SkillInstallerTests` | 1 | hook/config install |
| `VersionComparisonTests` | 5 | pure unit (semver比較) |
| `SymlinkEntryPointTests` | 2 | shell/CLI |
| `InstallUninstallTests` | 3 | hook/config install (`~/.local/bin`, skills) |
| `GitUpdateTests` | 3 | git repository (一時 remote + clone) |
| `ArgumentForwardingTests` | 2 | shell/CLI |
| `HerdrAgentWorkerBackendTests` | 9 | Herdr mock (pane起動コマンド組み立て) |
| `DispatcherWorkflowTests` | 7 | git worktree (6本が real `run --once`、残り1本はunit) |
| `SecretaryDoesNotHoldCardsBackContractTests` / `SecretaryForbidsInProcessDelegationContractTests` | 6 | contract/doc-lock |
| `TestTierContractTests` | 3 | contract (fast/full収録範囲の固定、今回追加) |
| `tests/test_registry.py` | 17 | pure unit + ファイルロック |
| `tests/test_monitor.py` | 28 | monitor HTTP (実サーバ起動、`fast`/`full`両方で実行) |
| `tests/test_secretary_guard.py` | 43 | pure unit + subprocess (guard argv検査) |
| `tests/test_monitor_state.js` | 9 | Node frontend state |

LaunchAgent / PTY を直接起動する自動テストは無い (`InstallUninstallTests` の
LaunchAgent 相当箇所はplist生成のunit検証のみで、実LaunchAgentは起動しない)。
end-to-endは上記 `DispatcherWorkflowTests` の6本のみ。

コマンドがcardに明記されている場合はそちらを優先する。特に指定が無い通常の
worker rework 反復では fast、マージ直前には full を使う。

### 検証手順の記録 (この card での実施内容)

- test順序ランダム化 (`random.Random(seed).shuffle`) で3回実行し、state leak
  無し (全156本green) を確認済み。
- mutation spot-check: `parse_score` の score 判定を無効化 → 実際に6件の
  `DispatcherWorkflowTests` が fail することを確認 (回帰検出力あり)。今回追加した
  phase duration計測 (`last_timings`/`phase durations:` 行) 自体も、値を空文字に
  置換するmutationで新規追加した3アサーション (`test_no_conflict_merge_still_works`,
  `test_resolver_retries_on_low_review_score_then_passes`) がfailすることを確認
  (追加前はsilent coverage lossだった)。
- 上記いずれも実施後 `git diff` で mutation を revert 済み (`kanban.sh` は
  変更前と同一であることを `diff` で確認)。

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
