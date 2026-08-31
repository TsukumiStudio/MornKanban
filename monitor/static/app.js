(() => {
  "use strict";

  const STATES = ["todo", "doing", "review", "resolving", "blocked", "done", "failed"];
  const STATE_LABELS = {
    todo: "Todo", doing: "Doing", review: "Review", resolving: "Resolving",
    blocked: "Blocked", done: "Done", failed: "Failed",
  };
  const POLL_MS = 8000;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  const fmtTime = (epochSeconds) => {
    if (!epochSeconds) return "-";
    const d = new Date(epochSeconds * 1000);
    return d.toLocaleString();
  };

  async function fetchJSON(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || ("HTTP " + res.status));
    }
    return res.json();
  }

  const projectsSection = document.getElementById("projects-section");
  const projectsList = document.getElementById("projects-list");
  const boardSection = document.getElementById("board-section");
  const boardColumns = document.getElementById("board-columns");
  const boardTitle = document.getElementById("board-title");
  const activityList = document.getElementById("activity-list");
  const updatedAt = document.getElementById("updated-at");
  const backBtn = document.getElementById("back-btn");
  const refreshBtn = document.getElementById("refresh-btn");

  const modal = document.getElementById("card-modal");
  const modalTitle = document.getElementById("card-modal-title");
  const modalMeta = document.getElementById("card-modal-meta");
  const modalBody = document.getElementById("card-modal-body");
  const modalClose = document.getElementById("card-modal-close");

  let currentSlug = null; // null = list view; string = board view

  function renderProjects(data) {
    projectsList.textContent = "";
    for (const p of data.projects) {
      const card = el("button", "project-card");
      card.type = "button";
      card.appendChild(el("h3", null, p.name));
      card.appendChild(el("div", "path", p.root || ""));

      if (p.error) {
        card.appendChild(el("div", "project-error", "読み込み失敗: " + p.error));
      } else {
        const counts = el("div", "counts");
        for (const s of STATES) {
          counts.appendChild(el("span", null, STATE_LABELS[s] + ": " + (p.counts[s] ?? 0)));
        }
        card.appendChild(counts);

        const d = p.dispatcher || {};
        const badge = el("span", "dispatcher-badge " + (d.running ? "running" : d.stale ? "stale" : "stopped"));
        badge.textContent = d.running ? "dispatcher: running (pid " + d.pid + ")" : d.stale ? "dispatcher: stale lock" : "dispatcher: stopped";
        card.appendChild(badge);

        card.appendChild(el("div", "path", "最終活動: " + fmtTime(p.last_activity)));
      }

      card.addEventListener("click", () => openBoard(p.slug));
      projectsList.appendChild(card);
    }
  }

  function renderActivity(data) {
    activityList.textContent = "";
    for (const it of data.activity) {
      const li = el("li");
      const left = el("div");
      left.appendChild(el("div", null, it.project_name + " / " + STATE_LABELS[it.state] + ": " + it.title));
      const when = el("div", "when", fmtTime(it.mtime));
      li.appendChild(left);
      li.appendChild(when);
      li.addEventListener("click", () => openCard(it.project, it.state, it.filename));
      activityList.appendChild(li);
    }
  }

  async function openBoard(slug) {
    currentSlug = slug;
    projectsSection.hidden = true;
    boardSection.hidden = false;
    await refreshBoard();
  }

  function closeBoard() {
    currentSlug = null;
    boardSection.hidden = true;
    projectsSection.hidden = false;
  }

  async function refreshBoard() {
    if (!currentSlug) return;
    let data;
    try {
      data = await fetchJSON("/api/projects/" + encodeURIComponent(currentSlug));
    } catch (e) {
      boardTitle.textContent = "読み込み失敗";
      boardColumns.textContent = String(e.message || e);
      return;
    }
    boardTitle.textContent = data.name + " (" + data.root + ")";
    boardColumns.textContent = "";
    for (const s of STATES) {
      const col = el("div", "board-column");
      const h = el("h4");
      h.appendChild(el("span", "badge-" + s, STATE_LABELS[s]));
      h.appendChild(document.createTextNode(" (" + data.counts[s] + ")"));
      col.appendChild(h);
      for (const c of data.columns[s]) {
        const item = el("div", "card-item");
        item.appendChild(el("div", null, c.title || c.filename));
        item.appendChild(el("div", "card-sub", [c.backend, c.model, "attempts " + c.attempts + "/" + c.max_attempts].filter(Boolean).join(" · ")));
        item.addEventListener("click", () => openCard(currentSlug, s, c.filename));
        col.appendChild(item);
      }
      boardColumns.appendChild(col);
    }
  }

  async function openCard(slug, state, filename) {
    let data;
    try {
      data = await fetchJSON("/api/projects/" + encodeURIComponent(slug) + "/cards/" + encodeURIComponent(state) + "/" + encodeURIComponent(filename));
    } catch (e) {
      data = { frontmatter: {}, body: "読み込み失敗: " + (e.message || e) };
    }
    modalTitle.textContent = (data.frontmatter && data.frontmatter.title) || filename;
    modalMeta.textContent = "";
    for (const [k, v] of Object.entries(data.frontmatter || {})) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", null, k));
      tr.appendChild(el("td", null, v));
      modalMeta.appendChild(tr);
    }
    modalBody.textContent = data.body || "";
    modal.hidden = false;
  }

  modalClose.addEventListener("click", () => { modal.hidden = true; });
  modal.addEventListener("click", (ev) => { if (ev.target === modal) modal.hidden = true; });
  backBtn.addEventListener("click", closeBoard);
  refreshBtn.addEventListener("click", () => refreshAll(true));

  async function refreshAll(manual) {
    try {
      const [projects, activity] = await Promise.all([
        fetchJSON("/api/projects"),
        fetchJSON("/api/activity"),
      ]);
      renderProjects(projects);
      renderActivity(activity);
      if (currentSlug) await refreshBoard();
      updatedAt.textContent = "最終更新: " + new Date().toLocaleTimeString();
    } catch (e) {
      updatedAt.textContent = "更新失敗: " + (e.message || e);
    }
  }

  refreshAll(true);
  setInterval(() => refreshAll(false), POLL_MS);
})();
