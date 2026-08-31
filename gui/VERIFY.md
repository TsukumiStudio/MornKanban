# MornKanban GUI 検証レポート

検証開始: 2026-08-31

## 手順
1. gui/server.py をポート8777でバックグラウンド起動
2. curl による API 検証
3. ブラウザ検証 (任意)
4. サーバ停止・総合判定

---

## サーバ起動
PASS: `MORNKANBAN_GUI_PORT=8777 python3 gui/server.py` バックグラウンド起動成功。
ログ: `MornKanban GUI: http://127.0.0.1:8777/ (repo: .../20260831-160520-486)`

## 1. GET /api/status
PASS
```json
{"deps": {"herdr": true, "claude": true, "codex": true, "kanban": true}, "herdr_env": true, "repo": "/Users/matsufriends/git/MornKanban/.kanban/wt/20260831-160520-486"}
```
deps/herdr_env/repo すべて含む。

## 2. POST /api/projects
PASS
```json
{"ok": true, "project": {"path": "/tmp/kanban-gui-verify2", "name": "kanban-gui-verify2", "has_kanban": false}}
```

## 3. POST /api/init
PASS
```json
{"ok": true}
```
`/tmp/kanban-gui-verify2/.kanban` 生成確認 (KANBAN.md, .gitignore, doing/done/failed/review/todo ディレクトリ)。

## 4. GET/PUT /api/policy 往復一致
PASS
- GET で初期 KANBAN.md 内容を取得
- PUT で末尾に `<!-- verify-marker -->` を追記して保存 → `{"ok": true}`
- 再度 GET → 追記後の内容と完全一致 (`new_is_old_plus_marker: True`, `marker_in_new: True`)

## 5. POST /api/card
PASS
title=verify, body=test, backend/model/threshold=空文字で送信。
```json
{"ok": true, "file": "/private/tmp/kanban-gui-verify2/.kanban/todo/20260831-160633-18702-verify.md"}
```

## 6. GET /api/board
PASS
```json
{"states": {"todo": [{"id": "20260831-160633-18702", "title": "verify", "attempts": "0", "file": "/tmp/kanban-gui-verify2/.kanban/todo/20260831-160633-18702-verify.md"}], "doing": [], "review": [], "done": [], "failed": []}}
```
todo に作成した1枚が反映されている。

## 7. GET /api/card?file=
PASS
```json
{"content": "---\nid: 20260831-160633-18702\ntitle: verify\nbackend: auto\nmodel: sonnet\nthreshold: 80\nmax_attempts: 3\nattempts: 0\ncreated: 2026-08-31T16:06:33\n---\n\n## Task\n\ntest\n\n## History\n"}
```
本文 (`## Task` 直下の `test`) を含む content を返却。

---

## ブラウザ検証
未実施 — ワーカー環境からはブラウザ操作ツール (Chrome 拡張) に接続できないため。API 検証で代替。

## 総合判定
PASS
