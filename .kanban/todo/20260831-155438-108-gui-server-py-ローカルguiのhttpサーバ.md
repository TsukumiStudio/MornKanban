---
id: 20260831-155438-108
title: gui/server.py: ローカルGUIのHTTPサーバ
backend: claude
model: opus
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T15:54:38
---

## Task

## 目的
MornKanban のローカル Web GUI のバックエンド `gui/server.py` を新規作成する。
(-m opus 理由: 多エンドポイント API を一発で正確に実装する必要がある設計級カード)

## 制約
- python3 標準ライブラリのみ (pip 禁止)。単一ファイル gui/server.py
- http.server.ThreadingHTTPServer を 127.0.0.1 のみに bind。ポートは環境変数 MORNKANBAN_GUI_PORT (既定 8765)
- 静的ファイルは gui/static/ から / で配信 (index.html が既定)。gui/static/ が無ければ作らなくてよい (このカードでは server.py のみ作る)
- 外部コマンドは subprocess.run(timeout=30) で呼ぶ。REPO = このリポジトリのルート (server.py の2階層上)
- 全 API は JSON を返す。失敗は {"ok": false, "error": "<str>"} と HTTP 400/500

## API 契約 (フロントは別カードがこの契約だけを頼りに実装する。厳守)
- GET /api/status → {"deps":{"herdr":bool,"claude":bool,"codex":bool,"kanban":bool},"herdr_env":bool,"repo":"<REPO絶対パス>"}
  shutil.which で判定。kanban は which("kanban") または REPO/kanban.sh 存在で true。herdr_env は os.environ.get("HERDR_ENV")=="1"
- 設定ファイル ~/.config/mornkanban/gui.json = {"projects": ["<絶対パス>", ...]} (無ければ空扱い、書き込み時に親ディレクトリ作成)
- GET /api/projects → {"projects":[{"path":str,"name":basename,"has_kanban":bool}]} (存在しないディレクトリは除外)
- POST /api/projects {"path":str} → expanduser+abspath、実在ディレクトリでなければ 400。重複なしで設定に追加 → {"ok":true,"project":{...}}
- POST /api/init {"path":str} → subprocess [\"bash\", REPO+\"/kanban.sh\", \"init\", path] → {"ok":true}
- GET /api/policy?path=<proj> → {"content": <proj>/.kanban/KANBAN.md の中身} (無ければ 404)
- PUT /api/policy {"path","content"} → <proj>/.kanban/KANBAN.md へ書き込み → {"ok":true}
- GET /api/board?path=<proj> → {"states":{"todo":[card...],"doing":[...],"review":[...],"done":[...],"failed":[...]}}
  card = {"id","title","attempts","file"} 。<proj>/.kanban/<state>/*.md の先頭 frontmatter (1行目と2つ目の "---" の間) から "id: ","title: ","attempts: " を素朴に読む。file は絶対パス。mtime 昇順
- GET /api/card?file=<abs> → {"content": ファイル全文}。os.path.realpath が登録済みプロジェクトの .kanban/ 配下で始まらなければ 403
- POST /api/card {"path","title","body","backend","model","threshold"} → cwd=path で [\"bash\", REPO+\"/kanban.sh\", \"add\", title] + (backend が非空なら [\"-b\",backend]) + (model 非空なら [\"-m\",model]) + (threshold 非空なら [\"-t\",str(threshold)])、stdin=body → {"ok":true,"file":stdout.strip()}
- POST /api/secretary {"path"} → herdr_env でなければ 400。手順:
  1. subprocess: herdr pane split --current --direction right --cwd <path> --no-focus → stdout JSON の result.pane.pane_id
  2. name = "kanban-sec-" + 4桁hex乱数
  3. herdr agent start <name> --kind claude --pane <pane_id> --timeout 60000 (失敗しても続行し herdr agent wait <name> --timeout 30000 を試す)
  4. herdr agent prompt <name> "kanban の秘書として待機して"
  → {"ok":true,"pane":pane_id,"name":name}
- POST /api/dispatch {"path","jobs"} → herdr_env でなければ 400。herdr pane split --current --direction down --cwd <path> --no-focus で pane_id を取得し、herdr pane run <pane_id> <cmd> を実行。cmd は1つの文字列:
  KANBAN_WORKER_CMD=<REPO>/herdr-agent-worker.sh KANBAN_REVIEW_CMD='env KANBAN_HERDR_ROLE=reviewer <REPO>/herdr-agent-worker.sh' bash <REPO>/kanban.sh run -j <jobs>; exit
  → {"ok":true,"pane":pane_id}

## 完了条件・検証
- python3 -m py_compile gui/server.py が通る
- MORNKANBAN_GUI_PORT=8799 python3 gui/server.py をバックグラウンド起動し、curl -s http://127.0.0.1:8799/api/status が deps キーを含む JSON を返し、curl -s -X POST -d '{"path":"/tmp"}' http://127.0.0.1:8799/api/projects が ok:true を返すこと。検証後プロセスを kill すること

## History
