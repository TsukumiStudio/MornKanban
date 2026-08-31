# セットアップGUI 検証

開始時刻: 2026-08-31 16:47:42 JST

対象: gui/server.py (セットアップ専用 API へ全面改稿後)
ポート: 8803 (MORNKANBAN_GUI_PORT)
方法: curl による API 直叩き検証 (ブラウザ操作なし)

## サーバ起動

`MORNKANBAN_GUI_PORT=8803 python3 gui/server.py` をバックグラウンド起動。PID 52843。

## GET /api/status → PASS

deps{herdr,claude,codex,python3} / install{cli,skill} / repo / herdr_env をすべて含む。

```json
{"deps": {"herdr": true, "claude": true, "codex": true, "python3": true}, "install": {"cli": true, "skill": true}, "repo": "/Users/matsufriends/git/MornKanban/.kanban/wt/20260831-164725-9906", "herdr_env": true}
```
HTTP 200

## GET /api/board, /api/policy, /api/card → PASS

旧エンドポイント3件すべて 404。

```json
{"ok": false, "error": "no such endpoint: GET /api/board"}
{"ok": false, "error": "no such endpoint: GET /api/policy"}
{"ok": false, "error": "no such endpoint: GET /api/card"}
```

## POST /api/install/cli → PASS

`{"ok": true, "in_path": true}` (HTTP 200)。`~/.local/bin/kanban` がシンボリックリンクとして存在し、
このワークツリーの `kanban.sh` を指す。

```
lrwxr-xr-x ... /Users/matsufriends/.local/bin/kanban -> /Users/matsufriends/git/MornKanban/.kanban/wt/20260831-164725-9906/kanban.sh
```

## POST /api/install/skill (force なし) → PASS

既存スキルを上書きせず 409 を返す (force による上書きは実施していない)。

```json
{"ok": false, "error": "already installed (force で上書き)"}
```
HTTP 409

## プロジェクト登録 → init → 一覧反映 → PASS

`mkdir -p /tmp/kanban-gui-verify4` の後:

- POST /api/projects → `{"ok": true, "project": {"path": "/tmp/kanban-gui-verify4", "name": "kanban-gui-verify4", "has_kanban": false}}` (HTTP 200)
- POST /api/init → `{"ok": true}` (HTTP 200)、`/tmp/kanban-gui-verify4/.kanban` が生成される (KANBAN.md 等を含む)
- GET /api/projects → 一覧に `kanban-gui-verify4` が `has_kanban: true` で含まれる

```json
{"projects": [{"path": "/tmp/kanban-gui-verify", "name": "kanban-gui-verify", "has_kanban": true}, {"path": "/tmp/kanban-gui-verify2", "name": "kanban-gui-verify2", "has_kanban": true}, {"path": "/tmp/kanban-gui-verify4", "name": "kanban-gui-verify4", "has_kanban": true}]}
```
(既存の config に以前の検証プロジェクトが残っているが、今回の対象である verify4 は正しく反映)

## サーバ停止 → PASS

`kill 52843` 後、`pgrep -f gui/server.py` は 0件 (exit code 1)。

## 総合判定: PASS

全項目 PASS。セットアップ専用 API (`/api/status`, `/api/install/cli`, `/api/install/skill`, `/api/projects`, `/api/init`) は仕様通り動作し、
旧エンドポイント (`/api/board`, `/api/policy`, `/api/card`) は削除されて 404 を返すことを確認した。
コード修正は行っていない。

