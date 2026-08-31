---
id: 20260831-162123-12969
title: gui/static/index.html + style.css: セットアップ画面へ改稿
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T16:21:23
---

## Task

## 目的
gui/static/index.html と gui/static/style.css を「セットアップウィザード画面」へ全面的に書き換える。
ボード5列・カード追加フォーム・ポリシーエディタ・タブ・モーダル・秘書/ディスパッチャボタンは**全て削除**。
<script src="app.js" defer></script> と <link rel="stylesheet" href="style.css"> は維持 (app.js の中身は別カード)。

## 新 DOM 契約 (app.js がこの id を直接参照する。厳守)
- ヘッダ: タイトル "MornKanban Setup"、<div id="dep-badges"></div>
- ステップ1 (CLI): <section id="step-cli"> 内に説明文、<span id="install-cli-status"></span>、
  <button id="btn-install-cli">kanban CLI をインストール</button>
- ステップ2 (スキル): <section id="step-skill"> 内に説明文、<span id="install-skill-status"></span>、
  <button id="btn-install-skill">Claude Code スキルを導入</button>
- ステップ3 (プロジェクト): <section id="step-projects"> 内に
  <input id="project-path" placeholder="プロジェクトの絶対パス">、<button id="btn-add-project">追加</button>、
  <ul id="project-list"></ul> (JS が li を流し込む。各 li 内に init ボタンを JS が付ける)
- 案内: <section id="step-next"> に「導入後は Herdr のペインで claude を起動し『kanban の秘書として待機して』と
  一言送るだけ」という静的な説明文
- 通知: <div id="toast" hidden></div>

## スタイル要件
- 素の CSS。縦一列のステップカード (白背景・角丸・影)、最大幅 720px 中央寄せ
- ステータス span 用に .ok (緑) / .ng (赤) クラス。#toast は画面下部中央固定
- 完了済みステップは視覚的に分かる (JS が section に .done クラスを付ける想定)

## 完了条件・検証
- grep で上記 id (dep-badges, step-cli, install-cli-status, btn-install-cli, step-skill,
  install-skill-status, btn-install-skill, step-projects, project-path, btn-add-project,
  project-list, step-next, toast) が index.html に全て存在
- ボード/ポリシー/モーダル系の id (col-todo, policy-editor, card-modal 等) が残っていないこと

## History
