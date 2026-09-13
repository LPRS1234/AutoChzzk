const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");

const source = readFileSync(new URL("../chrome_extension/autoplay_page.js", `file://${__filename.replaceAll("\\", "/")}`), "utf8");

test("the auto-opened CHZZK page is woken without activating its browser tab", () => {
  const events = [];
  class SyntheticEvent {
    constructor(type) { this.type = type; }
  }
  const document = {
    hidden: true,
    webkitHidden: true,
    visibilityState: "hidden",
    webkitVisibilityState: "hidden",
    hasFocus() { return false; },
    dispatchEvent(event) { events.push(["document", event.type]); },
  };
  const window = {
    dispatchEvent(event) { events.push(["window", event.type]); },
  };

  vm.runInContext(source, vm.createContext({ document, window, Event: SyntheticEvent }));

  assert.equal(document.hidden, false);
  assert.equal(document.webkitHidden, false);
  assert.equal(document.visibilityState, "visible");
  assert.equal(document.webkitVisibilityState, "visible");
  assert.equal(document.hasFocus(), true);
  assert.deepEqual(events, [["document", "visibilitychange"], ["window", "focus"]]);
});
