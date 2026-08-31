# セットアップ検証

`setup_core.py` は CLI シンボリックリンクと Claude Code / Codex の
`kanban-dispatch` スキルを導入する。スキルの正本は
`skills/kanban-dispatch/` で、インストール時に MornKanban checkout の絶対パスと
`VERSION` の内容を埋め込む。バージョン管理は `setup_core.py` の `local_version()` /
`fetch_latest_version()` / `compare_versions()` / `run_update()` が担う。

`dashboard.py` はそのロジックを読み取り専用で使い、引数なしの
`kanban-setup.sh` に枠付きの状態カードを描画し、`h`入力時だけ操作ガイドを開く。TTYでは色とUnicode、
非TTY・`NO_COLOR`・`TERM=dumb`ではASCII・色なしへ切り替え、現在の`VERSION`を
常に明記する。`y`/`s`/`u` は変更プレビューと再確認後にだけ実行される。
本体セットアップ対象外のmonitorは`任意・未設定`、registryは件数に応じて
`登録なし`/`登録あり`と表示し、本体の`未導入`と混同しない。

## バージョンポリシー

- 配布バージョンの正本はリポジトリ直下の `VERSION` (`X.Y.Z`、セマンティック)。
- 「最新公開バージョン」は GitHub main 上の raw `VERSION` を指す。タグ・GitHub Releases
  が無いための代替。`KANBAN_VERSION_URL` (`file://` 可) で参照先を差し替えられる
  — ネットワーク無しのテストはこれを使う。
- `kanban --version` はローカルの `VERSION` を読むだけでネットワークに触れない。
- `kanban version` は current / latest / state (`up-to-date` / `update-available` /
  `local-ahead` / `unknown`) を表示する。
- `kanban update` は追跡済み・stage済みの変更 / detached HEAD / `main` 以外の
  ブランチを拒否し、未追跡ファイルは保持する (更新内容と衝突する場合はGitが拒否する)。
  `git pull --ff-only origin main` の後、更新後のインストーラを再読み込みして
  CLI とスキルを再導入する。

## 自動検証

```sh
# 反復中: 変更機能だけ (名前は複数指定可、上限60秒)
python3 tests/run.py targeted tests.test_setup_dashboard

# 一区切り: 軽い契約・pure logic・monitor/registry/guard (各stepに上限あり)
python3 tests/run.py fast

# mainへ統合する直前に1回だけ: 全Python + worker lifecycle + frontend + skill
python3 tests/run.py full
```

構文チェックは編集直後に対象ファイルだけ実行する。`tests/run.py` は各stepを別の
process groupで起動し、上限超過時は子・孫processも含めてTERM→KILLするため、
失敗したテストがdispatcher/workerを残留させない。

### テストの段階 (targeted / fast / full)

- **targeted**: 編集した機能のtest名だけ。通常の実装iterationはここを使う。
- **fast**: monitor / registry / guard / activity log と秘書contractの軽い集合。
  dispatcherの実worktree E2E、permission matrix、review toggle matrixは含めない。
- **full**: `unittest discover` で全Python testを漏れなく収集し、worker lifecycle、
  frontend、skill validationも実行する。**main統合直前の最終gateとして1回だけ。**
  reviewer は `review_enabled: false` またはcardの `--no-review` で省略できるが、
  reviewer OFFでも、マージ前full gateはworker自身または明示した検証ステップが
  必ず1回だけ実行する。

fast合格はfullの代替ではない。git worktree/merge/conflict/resolver/retry、権限matrix、
review on/off matrixなどの実結合はfullだけで網羅する。テスト本数は機能追加で変わる
ため固定値を文書へ複製せず、full実行時のunittest出力を正とする。

カードに検証コマンドが明記されている場合はまずtargetedとしてそれを使い、通常の
反復でfullを繰り返さない。

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
  追跡済み変更の拒否、未追跡ファイルの保持
- `kanban-setup.sh` が引数を `gui/setup_cli.py` へ確実に転送する
  (転送漏れは対話ウィザードへ黙って落ちる既知の失敗モード)
- セットアップダッシュボードが枠と現在VERSIONだけを起動表示し、`h`入力後にだけ
  操作ガイドを表示してメニューへ戻る。TTY/非TTY・色・狭幅のfallback、導入状態、
  変更プレビュー、確認拒否時の無変更も維持する
- 任意monitorの未設定と空registryを、本体セットアップ失敗の`未導入`として表示しない

セットアップ画面の非TTY経路は次で確認する。ASCII枠付きダッシュボードと
`VERSION:`を表示後、変更せず終了する。

```sh
python3 gui/setup_cli.py </dev/null
```

TTYでは引数なしで起動し、`h`=help / `y`=install / `s`=update /
`u`=uninstall / `N`=何もしない、を選ぶ。helpは操作ガイドを表示してメニューへ戻る。
各変更操作はプレビュー後にもう一度`[y/N]`で確認する。

`install` / `update` / `uninstall` / `version` は非対話でも直接呼べる:

```sh
python3 gui/setup_cli.py version
./kanban-setup.sh install
```
