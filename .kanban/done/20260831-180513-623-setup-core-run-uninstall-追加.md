---
id: 20260831-180513-623
title: setup_core: run_uninstall() 追加
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
created: 2026-08-31T18:05:13
---

## Task

## 目的
gui/setup_core.py にアンインストール機能を追加する。他ファイルは触らない。

## run_uninstall() -> list[str] の仕様 (冪等・安全側)
- in_worktree() なら ["refused: kanban worktree 内"] を返すだけ
- CLI symlink (~/.local/bin/kanban):
  - 存在しない → "CLI: 未導入"
  - symlink でリンク先の realpath が REPO 配下 → 削除して "CLI: 削除しました"
  - それ以外 (通常ファイル・他リポジトリを指す symlink) → 削除せず "CLI: このインストーラの管理物ではないため残しました"
- スキル (~/.claude/skills/kanban-dispatch/):
  - SKILL.md が無い → "スキル: 未導入"
  - SKILL.md の内容に "MornKanban" を含む (インストーラ製の目印) → ディレクトリごと削除して "スキル: 削除しました"
  - 含まない → 削除せず "スキル: 別管理のスキルのため残しました"
- 例外は握りつぶさず (False 系メッセージに変換して) リストで返す。戻り値は実施結果メッセージのリスト

## 完了条件・検証
- python3 -m py_compile gui/setup_core.py
- worktree 内での python3 -c "...; print(setup_core.run_uninstall())" が refused を返す (実削除は走らない)
- 単体ロジックの確認として、HOME を一時ディレクトリに差し替えた subprocess
  (env HOME=/tmp/uninst-test python3 -c ...) で「未導入」2件が返ることを確認 (実環境の ~/. には触らない)

## History

### 2026-08-31 18:13:16 worker output (tail)

❯ 
──────────────────────────────────────────────────────────────────────────────────
  20260831-180513-623  kanban/20260831-180513-623  Sonnet 5                  /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
## 実装

`gui/setup_core.py` に `run_uninstall() -> list[str]` を追加した。

- `in_worktree()` なら `["refused: kanban worktree 内"]` を返す
- `_uninstall_cli()`: CLI symlink (`~/.local/bin/kanban`) の状態別処理
  - 未存在 → "CLI: 未導入"
  - symlink かつリンク先 realpath が REPO 配下 → 削除して "CLI: 削除しました"
  - それ以外 → "CLI: このインストーラの管理物ではないため残しました"
- `_uninstall_skill()`: スキル (`~/.claude/skills/kanban-dispatch/`) の状態別処理
  - SKILL.md 無し → "スキル: 未導入"
  - 内容に "MornKanban" を含む → ディレクトリごと削除して "スキル: 削除しました"
  - 含まない → "スキル: 別管理のスキルのため残しました"
- 各処理は例外を握りつぶさず、失敗メッセージ文字列に変換して返す

他ファイルは変更していない。

## 検証結果

```
$ python3 -m py_compile gui/setup_core.py
COMPILE_OK

$ python3 -c "...; print(setup_core.run_uninstall())"
['refused: kanban worktree 内']

$ env HOME=/tmp/uninst-test python3 -c "...; print(setup_core._uninstall_cli()); print(setup_core._uninstall_skill())"
CLI: 未導入
スキル: 未導入
```

実環境の `~/.local/bin/kanban` / `~/.claude/skills/kanban-dispatch/` には触れていない (worktree 内実行のため `run_uninstall()` 自体は refused で終了、HOME 差し替えテストは `/tmp/uninst-test` のみに作用)。

## 作業中に発生した事故と対処

編集の初回試行で `file_path` を worktree 側 (`.kanban/wt/20260831-180513-623/gui/setup_core.py`) ではなく誤ってメインリポジトリ側 (`/Users/matsufriends/git/MornKanban/gui/setup_core.py`) に指定し、そちらに同じ変更を書き込んでしまった。`git -C /Users/matsufriends/git/MornKanban checkout -- gui/setup_core.py` で revert 済み、影響なし。以降は worktree 側の絶対パスを明示して作業した。

### 2026-08-31 18:13:54 review

score: 92 / threshold: 80

実装・検証とも仕様通り。gui/setup_core.py のみ変更、run_uninstall/_uninstall_cli/_uninstall_skill は仕様の分岐(未導入/REPO配下symlink削除/管理外は残す/SKILL.mdにMornKanban文字列を含むか/例外を握りつぶさずメッセージ化)を満たす。py_compile OK、worktree内でrefused、HOME差し替えで両方「未導入」を確認済み。軽微な指摘: SKILL.md内の"MornKanban"検出はテンプレートに直接埋め込んだ目印ではなく、REPOパス(ディレクトリ名がたまたまMornKanban)の埋め込みに依存しており、リポジトリ名が変わると誤判定しうる暗黙依存だが、現状の要求は満たしている。
