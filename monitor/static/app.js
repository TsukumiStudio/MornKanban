(() => {
  "use strict";

  const STATES = ["todo", "doing", "review", "resolving", "blocked", "done", "failed"];
  const STATE_LABELS = {
    todo: "Todo", doing: "Doing", review: "Review", resolving: "Resolving",
    blocked: "Blocked", done: "Done", failed: "Failed",
  };
  const POLL_MS = 1000;

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

  async function fetchJSON(url, signal) {
    const res = await fetch(url, { cache: "no-store", signal });
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
  const agentActivityList = document.getElementById("agent-activity-list");
  const activityList = document.getElementById("activity-list");
  const updatedAt = document.getElementById("updated-at");
  const backBtn = document.getElementById("back-btn");
  const refreshBtn = document.getElementById("refresh-btn");

  const modal = document.getElementById("card-modal");
  const modalTitle = document.getElementById("card-modal-title");
  const modalMeta = document.getElementById("card-modal-meta");
  const modalBody = document.getElementById("card-modal-body");
  const modalClose = document.getElementById("card-modal-close");

  const state = window.MonitorState.createState();
  let boardAbort = null;
  let modalAbort = null;
  let refreshRunning = false;

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

  // --- board (selected project) ------------------------------------------

  function renderBoardSkeleton() {
    boardColumns.textContent = "";
    for (const s of STATES) {
      const col = el("div", "board-column");
      const h = el("h4");
      h.appendChild(el("span", "badge-" + s, STATE_LABELS[s]));
      col.appendChild(h);
      const items = el("div", "board-column-items");
      items.appendChild(el("div", "board-skeleton-item"));
      items.appendChild(el("div", "board-skeleton-item"));
      col.appendChild(items);
      boardColumns.appendChild(col);
    }
  }

  function renderBoardErrorPanel(slug, message) {
    boardColumns.textContent = "";
    const panel = el("div", "board-error-panel");
    panel.appendChild(el("div", "board-error-message", "読み込み失敗: " + message));
    const retry = el("button", null, "再読み込み");
    retry.type = "button";
    retry.addEventListener("click", () => openBoard(slug));
    panel.appendChild(retry);
    boardColumns.appendChild(panel);
  }

  function renderBoardColumns(data) {
    const scrollPositions = {};
    for (const items of boardColumns.querySelectorAll(".board-column-items[data-state]")) {
      scrollPositions[items.dataset.state] = items.scrollTop;
    }
    boardColumns.textContent = "";
    for (const s of STATES) {
      const col = el("div", "board-column");
      const h = el("h4");
      h.appendChild(el("span", "badge-" + s, STATE_LABELS[s]));
      h.appendChild(document.createTextNode(" (" + data.counts[s] + ")"));
      col.appendChild(h);
      const items = el("div", "board-column-items");
      items.dataset.state = s;
      for (const c of data.columns[s]) {
        const item = el("div", "card-item");
        item.appendChild(el("div", null, c.title || c.filename));
        if (s === "blocked" && c.blocked_kind === "review_infra") {
          item.appendChild(el("div", "card-sub badge-infra-blocked", "review infrastructure stopped (not a code failure)"));
        } else if (s === "blocked" && c.blocked_kind === "dependency") {
          item.appendChild(el("div", "card-sub", "dependency " + c.depends_on + " is " + c.dependency_state));
        }
        if (s === "failed" && c.failure_kind) {
          item.appendChild(el("div", "card-sub", "failure kind: " + c.failure_kind + " (inspect History; not automatically a product verdict)"));
        }
        const reviewLabel = c.review_enabled === "false" ? "Review: OFF" : "Review: ON";
        item.appendChild(el("div", "card-sub", [c.backend, c.model, "attempts " + c.attempts + "/" + c.max_attempts, reviewLabel].filter(Boolean).join(" · ")));
        item.addEventListener("click", () => openCard(state.selectedSlug, s, c.filename));
        items.appendChild(item);
      }
      col.appendChild(items);
      boardColumns.appendChild(col);
      items.scrollTop = scrollPositions[s] || 0;
    }
  }

  function renderAgentActivity(events) {
    agentActivityList.textContent = "";
    if (!events || !events.length) {
      agentActivityList.appendChild(el("div", "agent-activity-empty", "まだ実行ログはありません"));
      return;
    }
    for (const event of events) {
      const row = el("div", "agent-activity-row");
      const identity = [event.card_id, event.role, "attempt " + event.attempt].filter(Boolean).join(" · ");
      const runtime = [event.backend, event.model, event.agent_name, event.pane_id].filter(Boolean).join(" / ");
      const main = el("div");
      main.appendChild(el("div", "agent-activity-event", event.event + (event.status ? " · " + event.status : "")));
      main.appendChild(el("div", "agent-activity-meta", identity));
      main.appendChild(el("div", "agent-activity-meta", runtime));
      row.appendChild(main);
      row.appendChild(el("div", "agent-activity-time", fmtTime(event.timestamp) + " · " + (event.duration_secs || 0) + "s"));
      agentActivityList.appendChild(row);
    }
  }

  function renderBoardFromState() {
    boardSection.classList.toggle("is-loading", state.board.status === "loading");
    boardSection.classList.toggle("is-error", state.board.status === "error");

    if (state.view !== "board") return;
    const slug = state.selectedSlug;

    if (state.board.status === "loading") {
      boardTitle.textContent = "読み込み中: " + slug;
      agentActivityList.textContent = "";
      renderBoardSkeleton();
      return;
    }
    if (state.board.status === "error") {
      boardTitle.textContent = slug + " (読み込み失敗)";
      agentActivityList.textContent = "";
      renderBoardErrorPanel(slug, state.board.error);
      return;
    }
    const data = state.board.data;
    boardTitle.textContent = data.name + " (" + data.root + ")";
    renderBoardColumns(data);
    renderAgentActivity(data.agent_activity);
  }

  async function fetchBoard(slug, generation, signal) {
    let data;
    try {
      data = await fetchJSON("/api/projects/" + encodeURIComponent(slug), signal);
    } catch (e) {
      if (e.name === "AbortError") return;
      if (window.MonitorState.receiveBoardError(state, slug, generation, e.message || String(e))) {
        renderBoardFromState();
      }
      return;
    }
    if (window.MonitorState.receiveBoardSuccess(state, slug, generation, data)) {
      renderBoardFromState();
    }
  }

  async function openBoard(slug) {
    if (boardAbort) boardAbort.abort();
    boardAbort = new AbortController();
    const generation = window.MonitorState.selectProject(state, slug);
    projectsSection.hidden = true;
    boardSection.hidden = false;
    renderBoardFromState();
    renderModalFromState();
    await fetchBoard(slug, generation, boardAbort.signal);
  }

  function closeBoard() {
    if (boardAbort) { boardAbort.abort(); boardAbort = null; }
    if (modalAbort) { modalAbort.abort(); modalAbort = null; }
    window.MonitorState.deselectProject(state);
    boardSection.hidden = true;
    projectsSection.hidden = false;
    renderModalFromState();
  }

  async function refreshBoardData() {
    if (!state.selectedSlug || state.board.status === "loading") return;
    const slug = state.selectedSlug;
    const generation = state.board.generation;
    if (boardAbort) boardAbort.abort();
    boardAbort = new AbortController();
    await fetchBoard(slug, generation, boardAbort.signal);
  }

  // --- card detail modal ---------------------------------------------------

  function renderModalFromState() {
    if (!state.modal.open) {
      modal.hidden = true;
      return;
    }
    modal.hidden = false;

    if (state.modal.status === "loading") {
      modalTitle.textContent = "読み込み中: " + state.modal.filename;
      modalMeta.textContent = "";
      modalBody.textContent = "";
      return;
    }
    if (state.modal.status === "error") {
      modalTitle.textContent = state.modal.filename;
      modalMeta.textContent = "";
      modalBody.textContent = "読み込み失敗: " + state.modal.error;
      return;
    }
    const data = state.modal.data;
    modalTitle.textContent = (data.frontmatter && data.frontmatter.title) || state.modal.filename;
    modalMeta.textContent = "";
    for (const [k, v] of Object.entries(data.frontmatter || {})) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", null, k));
      tr.appendChild(el("td", null, v));
      modalMeta.appendChild(tr);
    }
    modalBody.textContent = data.body || "";
  }

  async function openCard(slug, cardState, filename) {
    if (modalAbort) modalAbort.abort();
    modalAbort = new AbortController();
    const generation = window.MonitorState.openCard(state, slug, cardState, filename);
    renderModalFromState();

    let data;
    try {
      data = await fetchJSON(
        "/api/projects/" + encodeURIComponent(slug) + "/cards/" + encodeURIComponent(cardState) + "/" + encodeURIComponent(filename),
        modalAbort.signal
      );
    } catch (e) {
      if (e.name === "AbortError") return;
      if (window.MonitorState.receiveCardError(state, generation, e.message || String(e))) {
        renderModalFromState();
      }
      return;
    }
    if (window.MonitorState.receiveCardSuccess(state, generation, data)) {
      renderModalFromState();
    }
  }

  function closeModalUI() {
    if (modalAbort) { modalAbort.abort(); modalAbort = null; }
    window.MonitorState.closeModal(state);
    renderModalFromState();
  }

  modalClose.addEventListener("click", closeModalUI);
  modal.addEventListener("click", (ev) => { if (ev.target === modal) closeModalUI(); });
  backBtn.addEventListener("click", closeBoard);
  refreshBtn.addEventListener("click", refreshAll);

  async function refreshAll() {
    if (refreshRunning) return;
    refreshRunning = true;
    try {
      const [projects, activity] = await Promise.all([
        fetchJSON("/api/projects"),
        fetchJSON("/api/activity"),
      ]);
      renderProjects(projects);
      renderActivity(activity);
      await refreshBoardData();
      updatedAt.textContent = "最終更新: " + new Date().toLocaleTimeString();
    } catch (e) {
      updatedAt.textContent = "更新失敗: " + (e.message || e);
    } finally {
      refreshRunning = false;
    }
  }

  refreshAll();
  setInterval(refreshAll, POLL_MS);
})();
