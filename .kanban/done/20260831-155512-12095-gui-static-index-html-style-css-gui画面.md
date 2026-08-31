---
id: 20260831-155512-12095
title: gui/static/index.html + style.css: GUI画面
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 2
created: 2026-08-31T15:55:12
---

## Task

## 目的
MornKanban ローカル GUI のフロント画面 `gui/static/index.html` と `gui/static/style.css` を新規作成する。
JS は別カードが `gui/static/app.js` を作る。このカードでは <script src="app.js" defer></script> と
<link rel="stylesheet" href="style.css"> を書くだけで app.js の中身は作らない。

## DOM 契約 (app.js はこの id を直接参照する。厳守)
- ヘッダ: タイトル "MornKanban"、依存状態表示 <div id="dep-badges"></div> (JS が span を流し込む)
- プロジェクト行: <select id="project-list"></select>、<input id="project-path" placeholder="プロジェクトの絶対パス">、
  <button id="btn-add-project">追加</button>、<button id="btn-init">kanban init</button>
- 操作行: <button id="btn-secretary">秘書を起動</button>、<input id="jobs-input" type="number" value="2" min="1" max="8">、
  <button id="btn-dispatch">ディスパッチャ起動</button>
- タブ: <button id="tab-board">ボード</button> <button id="tab-policy">ポリシー</button>、
  切替対象 <section id="view-board"> と <section id="view-policy"> (policy は hidden 初期)
- ボード: <div id="board"> 内に5列。各列は見出し (todo/doing/review/done/failed) と <ul id="col-todo"> 等
  (col-todo col-doing col-review col-done col-failed)。JS が <li class="card"> を流し込む
- カード追加フォーム (view-board 内): <input id="card-title">、<textarea id="card-body" rows="6">、
  <select id="card-backend"> (option: 空="auto既定", claude, codex)、<input id="card-model" placeholder="model (空=既定)">、
  <button id="btn-add-card">カード追加</button>
- ポリシー: <textarea id="policy-editor"></textarea>、<button id="btn-save-policy">保存</button>
- モーダル: <div id="card-modal" hidden><div class="modal-box"><button id="modal-close">×</button><pre id="modal-content"></pre></div></div>
- 通知: <div id="toast" hidden></div>

## スタイル要件 (style.css)
- 素の CSS のみ。システムフォント、ライトテーマでよい
- 5列ボードは横並び (flex)、各列は等幅・縦スクロール可、列ごとに薄い背景色 (failed は薄赤、done は薄緑)
- .card は白背景・角丸・影・カーソル pointer。#toast は画面下部中央固定
- #card-modal は全画面オーバーレイ (半透明黒) 中央に .modal-box (白、最大幅 720px、pre は横スクロール)

## 完了条件・検証
- 2ファイルが存在し、index.html に上記の全 id が1つずつ存在すること (grep で確認)
- python3 -c "import html.parser" 等は不要。tidy が無くてもよいので、grep -c 'id="' index.html で 20 以上あることを確認

## History

### 2026-08-31 15:56:24 worker output (tail)

        del, btn-add-
     … +20 lines

⏺ 完了条件を満たす2ファイルを作成し、回答ファ
  イルにも記録済み。

✻ Cooked for 40s · done 3:56 PM
                          ◐ medium · /effort
──────────────────────────────────────────────
❯ 
──────────────────────────────────────────────
  static  kanban/20260831-155512-12095  Son…
  ⏵⏵ accept edits on (shift+tab to cycle) ·
## 結果

- `gui/static/index.html` `gui/static/style.css` を新規作成
- DOM契約の全id (dep-badges, project-list, project-path, btn-add-project, btn-init, btn-secretary, jobs-input, btn-dispatch, tab-board, tab-policy, view-board, view-policy, board, col-todo, col-doing, col-review, col-done, col-failed, card-title, card-body, card-backend, card-model, btn-add-card, policy-editor, btn-save-policy, card-modal, modal-close, modal-content, toast) が各1つずつ存在
- `grep -c 'id="' index.html` = 34 (>=20 を満たす)
- `<script src="app.js" defer>` と `<link rel="stylesheet" href="style.css">` のみ記述、app.js本体は作成せず (別カードが担当)
- style.css は素のCSS。5列ボードは flex 横並び、各列は縦スクロール可、failed=薄赤背景、done=薄緑背景。.card は白背景・角丸・影・cursor pointer。#toast は画面下部中央固定。#card-modal は全画面半透明黒オーバーレイ中央に .modal-box (白、最大幅720px、pre 横スクロール)

### 2026-08-31 15:57:10 review

score: 0 / threshold: 80

reviewer output was not parseable JSON: y, #card-modal { display: flex }' の代わりに '#view-policy:not([hidden]), #card-modal:not([hidden]) { display: flex }' 等の修正が必要。#toast は該当ルールがなく問題なし。"}

### 2026-08-31 15:57:10 rework instruction (fix these points)

reviewer output was not parseable JSON: y, #card-modal { display: flex }' の代わりに '#view-policy:not([hidden]), #card-modal:not([hidden]) { display: flex }' 等の修正が必要。#toast は該当ルールがなく問題なし。"}

### 2026-08-31 15:57:54 worker output (tail)

    #card-modal の display: flex を
    :not([hidden]) に分離し、hidden属性による
    非表示が効くよう修正
  - 回答ファイル .kanban-answer.md
    に結果を記録済み

✻ Crunched for 37s · done 3:57 PM

──────────────────────────────────────────────
❯ 
──────────────────────────────────────────────
  20260831-155512-12095  kanban/20260831-15…
  ⏵⏵ accept edits on (shift+tab to cycle) ·
## 結果

- レビュー指摘を修正: `gui/static/style.css` の `#view-policy`, `#card-modal` に対する `display: flex` をベタ指定していたため `hidden` 属性が効かず常時表示になっていた
- `#view-policy { display: flex; ... }` → `#view-policy { padding: 16px 20px; }` + `#view-policy:not([hidden]) { display: flex; flex-direction: column; gap: 8px; }` に分離
- `#card-modal { display: flex; ... }` → `#card-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100; }` + `#card-modal:not([hidden]) { display: flex; align-items: center; justify-content: center; }` に分離
- `#toast` は該当ルールなし、修正不要 (レビュー指摘どおり)
- `grep -c 'id="' gui/static/index.html` = 34 (>=20 を満たす)
- DOM契約の全id (dep-badges, project-list, project-path, btn-add-project, btn-init, btn-secretary, jobs-input, btn-dispatch, tab-board, tab-policy, view-board, view-policy, board, col-todo, col-doing, col-review, col-done, col-failed, card-title, card-body, card-backend, card-model, btn-add-card, policy-editor, btn-save-policy, card-modal, modal-close, modal-content, toast) が各1つずつ存在済み
- `<script src="app.js" defer>` と `<link rel="stylesheet" href="style.css">` のみ記述、app.js本体は未作成 (別カード担当)

### 2026-08-31 15:58:28 review

score: 100 / threshold: 80

全DOM契約idが1つずつ存在(grep -c 'id="' index.html = 34 >= 20)。前回のレビュー指摘(#view-policy/#card-modalのdisplay:flexベタ指定によりhidden属性が効かない問題)は :not([hidden]) セレクタへの分離で解消済み。CSS要件(5列flex横並び、列ごと縦スクロール、failed=薄赤/done=薄緑、.cardの白背景・角丸・影・cursor pointer、#toast下部中央固定、#card-modal全画面半透明オーバーレイ+中央.modal-box最大幅720px+pre横スクロール)を全て満たす。app.jsは未作成でスコープ遵守。問題なし。
