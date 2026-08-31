---
id: 20260831-155512-12095
title: gui/static/index.html + style.css: GUI画面
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
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
