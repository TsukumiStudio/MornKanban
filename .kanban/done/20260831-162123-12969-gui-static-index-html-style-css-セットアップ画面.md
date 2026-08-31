---
id: 20260831-162123-12969
title: gui/static/index.html + style.css: セットアップ画面へ改稿
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 1
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

### 2026-08-31 16:22:43 worker output (tail)


  ⎿  Wrote 10 lines to .kanban-answer.md
       1 ## 完了
       3 - gui/static/index.html をセットアップウィザード画面へ全面書き換え。ボード5列
         タンは全削除。
         -install-cli, step-skill, install-skill-status, btn-install-skill, step-projec
       5 - `<script src="app.js" defer></script>` と `<link rel="stylesheet" href="styl
       6 - gui/static/style.css も全面書き換え。縦一列ステップカード (白背景・角丸・影)
         step-card.done`で完了表示。
       8 ## 検証
      10 grep で必須id全13個の存在、および禁止id (col-todo, policy-editor, card-modal,
         済み。全てOK。
     … +9 lines

⏺ - gui/static/index.html: セットアップウィザード画面へ全面書き換え、必須id13個をすべて実装、
    禁止id(ボード/ポリシー/モーダル系)を全削除
  - gui/static/style.css:
    縦一列ステップカードUIへ全面書き換え、.ok/.ng/#toast/.step-card.doneを実装
  - grep検証で完了条件をすべて満たすことを確認済み

✻ Brewed for 50s · done 4:22 PM

──────────────────────────────────────────────────────────────────────────────────────────────
❯ 
──────────────────────────────────────────────────────────────────────────────────────────────
  20260831-162123-12969  kanban/20260831-162123-12969  Sonnet 5                          /rc
  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents
## 完了

- gui/static/index.html をセットアップウィザード画面へ全面書き換え。ボード5列・カード追加フォーム・ポリシーエディタ・タブ・モーダル・秘書/ディスパッチャボタンは全削除。
- 新 DOM 契約の id をすべて実装: dep-badges, step-cli, install-cli-status, btn-install-cli, step-skill, install-skill-status, btn-install-skill, step-projects, project-path, btn-add-project, project-list, step-next, toast。
- `<script src="app.js" defer></script>` と `<link rel="stylesheet" href="style.css">` は維持。
- gui/static/style.css も全面書き換え。縦一列ステップカード (白背景・角丸・影)、最大幅720px中央寄せ、`.ok`/`.ng`ステータス色、`#toast`は画面下部中央固定、`.step-card.done`で完了表示。

## 検証

grep で必須id全13個の存在、および禁止id (col-todo, policy-editor, card-modal, btn-secretary, btn-dispatch, tab-board, tab-policy, card-form) の非存在を確認済み。全てOK。

### 2026-08-31 16:23:10 review

score: 95 / threshold: 80

index.html/style.cssとも要件通り。必須id13個(dep-badges, step-cli, install-cli-status, btn-install-cli, step-skill, install-skill-status, btn-install-skill, step-projects, project-path, btn-add-project, project-list, step-next, toast)全て実在し、禁止id(ボード/ポリシー/モーダル/タブ/秘書/ディスパッチャ系)は残っていない。script/linkタグも維持。CSSは縦一列ステップカード・最大幅720px中央寄せ・.ok/.ng・#toast固定・.step-card.doneの視覚区別を実装済みで完了条件を満たす。減点理由: 大きな欠陥はないが、.step-card.doneがボーダー色変更のみで簡素、install-cli-status/install-skill-status自体には初期テキストがなくJS依存が強い(仕様上は問題ないが目視確認は困難)。
