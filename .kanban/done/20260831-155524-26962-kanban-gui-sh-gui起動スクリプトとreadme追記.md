---
id: 20260831-155524-26962
title: kanban-gui.sh: GUI起動スクリプトとREADME追記
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
created: 2026-08-31T15:55:24
---

## Task

## 目的
1. リポジトリ直下に `kanban-gui.sh` を新規作成 (実行権付与)。
2. README.md に「## GUI」節を追記する。

## kanban-gui.sh の仕様
- bash 3.2 互換、set -euo pipefail
- REPO は自身のあるディレクトリ (cd "$(dirname "$0")" && pwd)
- python3 が無ければエラー終了。gui/server.py が無ければエラー終了
- 環境変数 MORNKANBAN_GUI_PORT (既定 8765) を尊重
- HERDR_ENV が 1 でなければ「Herdr ペイン内での起動を推奨 (秘書/ディスパッチャ起動ボタンが使えない)」と警告して続行
- (sleep 1 && open "http://127.0.0.1:$PORT") をバックグラウンドで仕込む (open コマンドがある場合のみ)
- exec で MORNKANBAN_GUI_PORT を渡しつつ python3 "$REPO/gui/server.py" をフォアグラウンド実行

## README の「## GUI」節 (## Setup 節の直後に挿入)
- 内容: git clone → Herdr のペインで ./kanban-gui.sh → ブラウザが開く → 画面から
  プロジェクト追加 / kanban init / ポリシー編集 / カード追加 / 秘書起動 / ディスパッチャ起動が
  ポチポチでできる、という説明を簡潔な英語で 5〜8 行程度
- 既存の他の節は変更しない

## 完了条件・検証
- bash -n kanban-gui.sh が通る、test -x kanban-gui.sh が真
- grep -q '## GUI' README.md

## History

### 2026-08-31 15:59:41 worker output (tail)



⏺ Write(.kanban-answer.md)
  ⎿  Wrote 11 lines to .kanban-answer.md
       1 - kanban-gui.sh を新規作成し実行権を付与した (bash 3.2 互換, set -euo pipefail
         )
       2   - REPO は自身のディレクトリ、python3/gui/server.py 不在時はエラー終了
       3   - MORNKANBAN_GUI_PORT (既定 8765) を尊重
       4   - HERDR_ENV!=1 のとき警告を出して続行
       6   - exec で python3 gui/server.py をフォアグラウンド実行
       7 - README.md の `## Setup` 直後に `## GUI` 節を追記 (英語, 5行)
       8 - 検証:
       9   - `bash -n kanban-gui.sh` 通過
      10   - `test -x kanban-gui.sh` 真
     … +1 line

⏺ - kanban-gui.sh 新規作成、実行権付与済み
  - README.md に ## Setup 直後 ## GUI 節を追記
  - bash -n / test -x / grep -q '## GUI' すべて通過

✻ Brewed for 32s · done 3:59 PM
                                                                          ◐ medium · /effort
──────────────────────────────────────────────────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────────────────────────────────────────────────
  20260831-155524-26962  kanban/20260831-155524-26962  Sonnet 5                          /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
- kanban-gui.sh を新規作成し実行権を付与した (bash 3.2 互換, set -euo pipefail)
  - REPO は自身のディレクトリ、python3/gui/server.py 不在時はエラー終了
  - MORNKANBAN_GUI_PORT (既定 8765) を尊重
  - HERDR_ENV!=1 のとき警告を出して続行
  - open コマンドがあればバックグラウンドで1秒後にブラウザを開く
  - exec で python3 gui/server.py をフォアグラウンド実行
- README.md の `## Setup` 直後に `## GUI` 節を追記 (英語, 5行)
- 検証:
  - `bash -n kanban-gui.sh` 通過
  - `test -x kanban-gui.sh` 真
  - `grep -q '## GUI' README.md` 一致

### 2026-08-31 16:00:12 review

score: 95 / threshold: 80

kanban-gui.sh の全要件を満たす: bash 3.2 互換, set -euo pipefail, REPO解決, python3/gui/server.py 不在時エラー終了, MORNKANBAN_GUI_PORT尊重, HERDR_ENV警告, open のバックグラウンド起動, exec でのフォアグラウンド実行。README.md の ## GUI 節も ## Setup 直後に追加され、英語5行で仕様の要素(clone→起動→ブラウザ→プロジェクト追加/init/ポリシー編集/カード追加/秘書起動/ディスパッチャ起動)を簡潔にカバー。bash -n / test -x / grep -q '## GUI' すべて通過、git diff は README.md と kanban-gui.sh のみで既存節は無改変。減点理由: README の GUI 節が『プロジェクト追加/カード追加』等の個別操作を一つの文にまとめており、仕様が挙げた6項目(プロジェクト追加, kanban init, ポリシー編集, カード追加, 秘書起動, ディスパッチャ起動)を明示的に列挙していない点はやや簡略化されているが許容範囲。
