(() => {
  'use strict';

  const el = {};

  function cacheEls() {
    const ids = [
      'dep-badges', 'step-cli', 'install-cli-status', 'btn-install-cli',
      'step-skill', 'install-skill-status', 'btn-install-skill',
      'step-projects', 'project-path', 'btn-add-project', 'project-list',
      'step-next', 'toast',
    ];
    for (const id of ids) {
      el[id] = document.getElementById(id);
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
      const err = new Error(errMsg);
      err.status = res.status;
      err.data = data;
      if (!(res.status === 409)) {
        toast(errMsg);
      }
      throw err;
    }
    return data;
  }

  function depBadge(name, ok) {
    const span = document.createElement('span');
    span.className = ok ? 'ok' : 'ng';
    span.textContent = name;
    return span;
  }

  let lastStatus = null;

  function updateDoneState() {
    if (!lastStatus) return;
    const install = lastStatus.install || {};
    if (install.cli) {
      el['step-cli'].classList.add('done');
    } else {
      el['step-cli'].classList.remove('done');
    }
    if (install.skill) {
      el['step-skill'].classList.add('done');
    } else {
      el['step-skill'].classList.remove('done');
    }
  }

  async function refresh() {
    await loadStatus();
    await loadProjects();
  }

  async function loadStatus() {
    const data = await api('GET', '/api/status');
    lastStatus = data;

    el['dep-badges'].innerHTML = '';
    const deps = data.deps || {};
    for (const [name, ok] of Object.entries(deps)) {
      el['dep-badges'].appendChild(depBadge(name, ok));
    }
    if (!deps.herdr || !deps.claude) {
      toast('herdr / claude が見つかりません');
    }

    const install = data.install || {};
    if (install.cli) {
      el['install-cli-status'].textContent = '導入済み';
      el['install-cli-status'].className = 'ok';
    } else {
      el['install-cli-status'].textContent = '未導入';
      el['install-cli-status'].className = 'ng';
    }
    if (install.skill) {
      el['install-skill-status'].textContent = '導入済み';
      el['install-skill-status'].className = 'ok';
    } else {
      el['install-skill-status'].textContent = '未導入';
      el['install-skill-status'].className = 'ng';
    }

    updateDoneState();
  }

  function renderProjects(projects) {
    el['project-list'].innerHTML = '';
    for (const p of projects) {
      const li = document.createElement('li');
      const label = document.createElement('span');
      label.textContent = `${p.name} (${p.path})`;
      li.appendChild(label);
      if (p.has_kanban) {
        const badge = document.createElement('span');
        badge.className = 'ok';
        badge.textContent = '導入済み';
        li.appendChild(badge);
      } else {
        const btn = document.createElement('button');
        btn.textContent = 'kanban init';
        btn.addEventListener('click', () => initProject(p.path));
        li.appendChild(btn);
      }
      el['project-list'].appendChild(li);
    }
  }

  async function loadProjects() {
    const data = await api('GET', '/api/projects');
    renderProjects(data.projects || []);
  }

  async function installCli() {
    const data = await api('POST', '/api/install/cli');
    if (data && data.in_path === false) {
      toast('~/.local/bin に PATH を通してください');
    }
    await refresh();
  }

  async function installSkill(force) {
    try {
      const data = await api('POST', '/api/install/skill', force ? { force: true } : {});
      await refresh();
      return data;
    } catch (e) {
      if (e.status === 409) {
        if (confirm('上書きしますか')) {
          await installSkill(true);
        }
        return null;
      }
      throw e;
    }
  }

  async function addProject() {
    const path = el['project-path'].value.trim();
    if (!path) return;
    await api('POST', '/api/projects', { path });
    el['project-path'].value = '';
    await refresh();
  }

  async function initProject(path) {
    await api('POST', '/api/init', { path });
    await refresh();
  }

  function bindEvents() {
    el['btn-install-cli'].addEventListener('click', installCli);
    el['btn-install-skill'].addEventListener('click', () => installSkill(false));
    el['btn-add-project'].addEventListener('click', addProject);
  }

  async function init() {
    cacheEls();
    bindEvents();
    try {
      await refresh();
    } catch (e) {
      /* toast already shown */
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
