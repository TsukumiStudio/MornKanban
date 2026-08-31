// Pure, DOM-free selection/loading state machine for the monitor UI.
//
// Every board fetch and card fetch is tagged with a generation number that
// is bumped whenever the user changes selection (openBoard/openCard/close).
// A response is only applied if the generation (and, for the board, the
// slug) it was issued for still matches the live state -- this is what
// keeps a slow/out-of-order response from a previous selection ("stale
// response") from ever overwriting what the user is currently looking at.
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.MonitorState = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function createState() {
    return {
      view: "list", // "list" | "board"
      selectedSlug: null,
      board: { generation: 0, status: "idle", data: null, error: null }, // status: idle|loading|loaded|error
      modal: {
        generation: 0,
        open: false,
        slug: null,
        state: null,
        filename: null,
        status: "idle", // idle|loading|loaded|error
        data: null,
        error: null,
      },
    };
  }

  function closeModal(state) {
    state.modal.generation += 1;
    state.modal.open = false;
    state.modal.slug = null;
    state.modal.state = null;
    state.modal.filename = null;
    state.modal.status = "idle";
    state.modal.data = null;
    state.modal.error = null;
  }

  // Selects (or reselects) a project's board. Synchronously clears any
  // previously loaded board data/error and any open card modal, and bumps
  // the board generation so in-flight requests from the old selection can
  // no longer land. Returns the generation the caller must tag its fetch
  // with.
  function selectProject(state, slug) {
    state.view = "board";
    state.selectedSlug = slug;
    state.board.generation += 1;
    state.board.status = "loading";
    state.board.data = null;
    state.board.error = null;
    closeModal(state);
    return state.board.generation;
  }

  function deselectProject(state) {
    state.view = "list";
    state.selectedSlug = null;
    state.board.generation += 1;
    state.board.status = "idle";
    state.board.data = null;
    state.board.error = null;
    closeModal(state);
  }

  function isBoardRequestCurrent(state, slug, generation) {
    return state.selectedSlug === slug && state.board.generation === generation;
  }

  function receiveBoardSuccess(state, slug, generation, data) {
    if (!isBoardRequestCurrent(state, slug, generation)) return false;
    state.board.status = "loaded";
    state.board.data = data;
    state.board.error = null;
    return true;
  }

  function receiveBoardError(state, slug, generation, error) {
    if (!isBoardRequestCurrent(state, slug, generation)) return false;
    state.board.status = "error";
    state.board.data = null;
    state.board.error = error;
    return true;
  }

  // Opens the card-detail modal. Independent from board selection (the
  // activity list can open a card belonging to a project that is not the
  // currently selected board), but still generation-guarded so a stale
  // response cannot repopulate a modal that has since moved on to another
  // card or been closed.
  function openCard(state, slug, cardState, filename) {
    state.modal.generation += 1;
    state.modal.open = true;
    state.modal.slug = slug;
    state.modal.state = cardState;
    state.modal.filename = filename;
    state.modal.status = "loading";
    state.modal.data = null;
    state.modal.error = null;
    return state.modal.generation;
  }

  function isModalRequestCurrent(state, generation) {
    return state.modal.open && state.modal.generation === generation;
  }

  function receiveCardSuccess(state, generation, data) {
    if (!isModalRequestCurrent(state, generation)) return false;
    state.modal.status = "loaded";
    state.modal.data = data;
    state.modal.error = null;
    return true;
  }

  function receiveCardError(state, generation, error) {
    if (!isModalRequestCurrent(state, generation)) return false;
    state.modal.status = "error";
    state.modal.data = null;
    state.modal.error = error;
    return true;
  }

  return {
    createState,
    selectProject,
    deselectProject,
    isBoardRequestCurrent,
    receiveBoardSuccess,
    receiveBoardError,
    openCard,
    closeModal,
    isModalRequestCurrent,
    receiveCardSuccess,
    receiveCardError,
  };
});
