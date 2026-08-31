// Node's built-in test runner only (no npm dependencies, no build step) --
// exercises monitor/static/state.js, the DOM-free selection/loading state
// machine behind the monitor Web UI. Run with:
//   node --test tests/test_monitor_state.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const MonitorState = require(path.join(__dirname, "..", "monitor", "static", "state.js"));

test("selecting project B while A is showing synchronously clears A's board", () => {
  const state = MonitorState.createState();
  const genA = MonitorState.selectProject(state, "a");
  MonitorState.receiveBoardSuccess(state, "a", genA, { name: "A", root: "/a", counts: {}, columns: {} });
  assert.equal(state.board.status, "loaded");

  MonitorState.selectProject(state, "b");
  assert.equal(state.selectedSlug, "b");
  assert.equal(state.board.status, "loading");
  assert.equal(state.board.data, null);
});

test("a stale response for A arriving after switching to B does not overwrite B", () => {
  const state = MonitorState.createState();
  const genA = MonitorState.selectProject(state, "a");
  MonitorState.selectProject(state, "b"); // B request has a new generation

  const applied = MonitorState.receiveBoardSuccess(state, "a", genA, { name: "A stale" });
  assert.equal(applied, false);
  assert.equal(state.selectedSlug, "b");
  assert.equal(state.board.data, null); // still B's loading state, not A's stale payload
});

test("rapid A -> B -> A leaves only the last A response applied", () => {
  const state = MonitorState.createState();
  const genA1 = MonitorState.selectProject(state, "a");
  MonitorState.selectProject(state, "b");
  const genA2 = MonitorState.selectProject(state, "a");

  // The first A request's late response must be dropped...
  const appliedFirst = MonitorState.receiveBoardSuccess(state, "a", genA1, { name: "A (old)" });
  assert.equal(appliedFirst, false);
  assert.equal(state.board.data, null);

  // ...while the second (current) A request's response is applied.
  const appliedSecond = MonitorState.receiveBoardSuccess(state, "a", genA2, { name: "A (new)" });
  assert.equal(appliedSecond, true);
  assert.equal(state.board.data.name, "A (new)");
});

test("fetch failure for the selected project shows error, never stale data", () => {
  const state = MonitorState.createState();
  const gen = MonitorState.selectProject(state, "a");
  MonitorState.receiveBoardSuccess(state, "a", gen, { name: "A" });

  // A poll re-fetch of the same selection fails.
  const applied = MonitorState.receiveBoardError(state, "a", gen, "network down");
  assert.equal(applied, true);
  assert.equal(state.board.status, "error");
  assert.equal(state.board.data, null);
  assert.equal(state.board.error, "network down");
});

test("an error response for a superseded selection is ignored", () => {
  const state = MonitorState.createState();
  const genA = MonitorState.selectProject(state, "a");
  MonitorState.selectProject(state, "b");

  const applied = MonitorState.receiveBoardError(state, "a", genA, "boom");
  assert.equal(applied, false);
  assert.notEqual(state.board.status, "error");
});

test("switching the board selection closes any open card modal", () => {
  const state = MonitorState.createState();
  MonitorState.selectProject(state, "a");
  const cardGen = MonitorState.openCard(state, "a", "todo", "card.md");
  MonitorState.receiveCardSuccess(state, cardGen, { frontmatter: {}, body: "hi" });
  assert.equal(state.modal.open, true);

  MonitorState.selectProject(state, "b");
  assert.equal(state.modal.open, false);

  // The old card fetch resolving late must not resurrect the modal.
  const applied = MonitorState.receiveCardSuccess(state, cardGen, { frontmatter: {}, body: "stale" });
  assert.equal(applied, false);
  assert.equal(state.modal.open, false);
});

test("a stale card-detail response never overwrites a newer card selection", () => {
  const state = MonitorState.createState();
  const genFirst = MonitorState.openCard(state, "a", "todo", "first.md");
  const genSecond = MonitorState.openCard(state, "a", "todo", "second.md");

  const appliedFirst = MonitorState.receiveCardSuccess(state, genFirst, { body: "first" });
  assert.equal(appliedFirst, false);

  const appliedSecond = MonitorState.receiveCardSuccess(state, genSecond, { body: "second" });
  assert.equal(appliedSecond, true);
  assert.equal(state.modal.data.body, "second");
});

test("card fetch failure surfaces error without reviving prior card data", () => {
  const state = MonitorState.createState();
  const genFirst = MonitorState.openCard(state, "a", "todo", "first.md");
  MonitorState.receiveCardSuccess(state, genFirst, { body: "first" });
  const genSecond = MonitorState.openCard(state, "a", "todo", "second.md");

  const applied = MonitorState.receiveCardError(state, genSecond, "not found");
  assert.equal(applied, true);
  assert.equal(state.modal.status, "error");
  assert.equal(state.modal.data, null);
});

test("deselecting the project resets to list view and clears board/modal", () => {
  const state = MonitorState.createState();
  const gen = MonitorState.selectProject(state, "a");
  MonitorState.receiveBoardSuccess(state, "a", gen, { name: "A" });
  MonitorState.openCard(state, "a", "todo", "c.md");

  MonitorState.deselectProject(state);
  assert.equal(state.view, "list");
  assert.equal(state.selectedSlug, null);
  assert.equal(state.board.status, "idle");
  assert.equal(state.board.data, null);
  assert.equal(state.modal.open, false);
});
