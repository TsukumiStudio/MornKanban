(() => {
  'use strict';

  const STATUSES = ['todo', 'doing', 'review', 'done', 'failed'];
  const BOARD_POLL_MS = 3000;

  const state = {
    projects: [],
    selectedPath: '',
    dispatchAllowed: true,
    boardTimer: null,
  };

  const el = {};

  function cacheEls() {
    const ids = [
      'dep-badges', 'project-list', 'project-path', 'btn-add-project', 'btn-init',
      'btn-secretary', 'jobs-input', 'btn-dispatch', 'tab-board', 'tab-policy',
      'view-board', 'view-policy', 'card-title', 'card-body', 'card-backend',
      'card-model', 'btn-add-card', 'policy-editor', 'btn-save-policy',
      'card-modal', 'modal-content', 'modal-close', 'toast',
    ];
    for (const id of ids) {
      el[id] = document.getElementById(id);
    }
    el.columns = {};
    for (const s of STATUSES) {
      el.columns[s] = document.getElementById(`col-${s}`);
    }
  }

  function toast(msg) {
    el.toast.textContent = msg;
    el.toast.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      el.toast.hidden = true;
    }, 3000);
  }

  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      toast(`通信エラー: ${e.message}`);
      throw e;
    }
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok || (data && data.ok === false)) {
      const errMsg = (data && data.error) || `HTTP ${res.status}`;
      toast(errMsg);
      throw new Error(errMsg);
    }
    return data;
  }

  function depBadge(name, ok) {
    const span = document.createElement('span');
    span.className = ok ? 'ok' : 'ng';
    span.textContent = name;
    return span;
  }

  async function loadStatus() {
    const data = await api('GET', '/api/status');
    el['dep-badges'].innerHTML = '';
    for (const [name, ok] of Object.entries(data.deps || {})) {
      el['dep-badges'].appendChild(depBadge(name, ok));
    }
    state.dispatchAllowed = !!data.herdr_env;
    if (!state.dispatchAllowed) {
      toast('Herdr ペイン内で起動してください (秘書/ディスパッチャ操作不可)');
      el['btn-secretary'].disabled = true;
      el['btn-dispatch'].disabled = true;
    } else {
      el['btn-secretary'].disabled = false;
      el['btn-dispatch'].disabled = false;
    }
  }

  async function loadProjects(selectPath) {
    const data = await api('GET', '/api/projects');
    state.projects = data.projects || [];
    el['project-list'].innerHTML = '';
    for (const p of state.projects) {
      const opt = document.createElement('option');
      opt.value = p.path;
      opt.textContent = p.has_kanban ? p.name : `${p.name} (未init)`;
      el['project-list'].appendChild(opt);
    }
    const target = selectPath || state.selectedPath || (state.projects[0] && state.projects[0].path) || '';
    if (target) {
      el['project-list'].value = target;
    }
    state.selectedPath = el['project-list'].value || '';
    await onProjectChanged();
  }

  async function onProjectChanged() {
    state.selectedPath = el['project-list'].value || '';
    if (!state.selectedPath) return;
    await loadBoard();
    if (!el['view-policy'].hidden) {
      await loadPolicy();
    }
  }

  function renderBoard(states) {
    for (const s of STATUSES) {
      const ul = el.columns[s];
      ul.innerHTML = '';
      const cards = (states && states[s]) || [];
      for (const card of cards) {
        const li = document.createElement('li');
        li.textContent = `${card.title} (${card.attempts})`;
        li.dataset.file = card.file;
        li.addEventListener('click', () => openCard(card.file));
        ul.appendChild(li);
      }
    }
  }

  async function loadBoard() {
    if (!state.selectedPath) return;
    const data = await api('GET', `/api/board?path=${encodeURIComponent(state.selectedPath)}`);
    renderBoard(data.states || {});
  }

  async function openCard(file) {
    const data = await api('GET', `/api/card?file=${encodeURIComponent(file)}`);
    el['modal-content'].textContent = data.content || '';
    el['card-modal'].hidden = false;
  }

  function closeCard() {
    el['card-modal'].hidden = true;
  }

  async function loadPolicy() {
    if (!state.selectedPath) return;
    const data = await api('GET', `/api/policy?path=${encodeURIComponent(state.selectedPath)}`);
    el['policy-editor'].value = data.content || '';
  }

  async function savePolicy() {
    if (!state.selectedPath) return;
    await api('PUT', '/api/policy', { path: state.selectedPath, content: el['policy-editor'].value });
    toast('ポリシーを保存しました');
  }

  async function addProject() {
    const path = el['project-path'].value.trim();
    if (!path) return;
    await api('POST', '/api/projects', { path });
    el['project-path'].value = '';
    await loadProjects(path);
  }

  async function initProject() {
    if (!state.selectedPath) return;
    await api('POST', '/api/init', { path: state.selectedPath });
    await loadProjects(state.selectedPath);
  }

  async function addCard() {
    const title = el['card-title'].value.trim();
    if (!title) return;
    if (!state.selectedPath) return;
    await api('POST', '/api/card', {
      path: state.selectedPath,
      title,
      body: el['card-body'].value,
      backend: el['card-backend'].value,
      model: el['card-model'].value,
      threshold: '',
    });
    el['card-title'].value = '';
    el['card-body'].value = '';
    el['card-backend'].value = '';
    el['card-model'].value = '';
    await loadBoard();
  }

  async function startSecretary() {
    if (!state.selectedPath) return;
    const data = await api('POST', '/api/secretary', { path: state.selectedPath });
    toast(`秘書を起動しました: ${(data && data.pane) || ''}`);
  }

  async function startDispatch() {
    if (!state.selectedPath) return;
    const data = await api('POST', '/api/dispatch', {
      path: state.selectedPath,
      jobs: el['jobs-input'].value,
    });
    toast(`ディスパッチャを起動しました: ${(data && data.pane) || ''}`);
  }

  function showBoardTab() {
    el['view-board'].hidden = false;
    el['view-policy'].hidden = true;
  }

  function showPolicyTab() {
    el['view-board'].hidden = true;
    el['view-policy'].hidden = false;
    loadPolicy();
  }

  function startBoardPolling() {
    if (state.boardTimer) clearInterval(state.boardTimer);
    state.boardTimer = setInterval(() => {
      loadBoard().catch(() => {});
    }, BOARD_POLL_MS);
  }

  function bindEvents() {
    el['project-list'].addEventListener('change', onProjectChanged);
    el['btn-add-project'].addEventListener('click', addProject);
    el['btn-init'].addEventListener('click', initProject);
    el['btn-add-card'].addEventListener('click', addCard);
    el['btn-secretary'].addEventListener('click', startSecretary);
    el['btn-dispatch'].addEventListener('click', startDispatch);
    el['btn-save-policy'].addEventListener('click', savePolicy);
    el['tab-board'].addEventListener('click', showBoardTab);
    el['tab-policy'].addEventListener('click', showPolicyTab);
    el['modal-close'].addEventListener('click', closeCard);
    el['card-modal'].addEventListener('click', (e) => {
      if (e.target === el['card-modal']) closeCard();
    });
  }

  async function init() {
    cacheEls();
    bindEvents();
    showBoardTab();
    try {
      await loadStatus();
    } catch (e) {
      /* toast already shown */
    }
    try {
      await loadProjects();
    } catch (e) {
      /* toast already shown */
    }
    startBoardPolling();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
