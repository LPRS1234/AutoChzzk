const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");

const source = readFileSync(new URL("../chrome_extension/content.js", `file://${__filename.replaceAll("\\", "/")}`), "utf8");

function makeContent() {
  const intervals = [];
  const timeouts = [];
  let messageListener;
  const page = { video: null, buttons: [] };
  const context = vm.createContext({
    chrome: {
      runtime: {
        id: "synthetic-extension",
        sendMessage() { return { catch() {} }; },
        onMessage: { addListener(listener) { messageListener = listener; } },
      },
    },
    document: {
      querySelector(selector) { return selector === "video" ? page.video : null; },
      querySelectorAll(selector) { return selector === "button" ? page.buttons : []; },
    },
    setInterval(callback, delay) {
      const timer = { callback, delay, cleared: false };
      intervals.push(timer);
      return timer;
    },
    clearInterval(timer) { timer.cleared = true; },
    setTimeout(callback, delay) {
      const timer = { callback, delay, cleared: false };
      timeouts.push(timer);
      return timer;
    },
    clearTimeout(timer) { timer.cleared = true; },
  });
  vm.runInContext(source, context);
  return {
    page,
    intervals,
    timeouts,
    send(message) { messageListener(message); },
  };
}

function makeVideo() {
  const attributes = new Map();
  const video = {
    paused: true,
    muted: false,
    defaultMuted: false,
    autoplay: false,
    playCalls: 0,
    setAttribute(name, value) { attributes.set(name, value); },
    play() {
      assert.equal(this.muted, true);
      assert.equal(this.defaultMuted, true);
      assert.equal(this.autoplay, true);
      assert.equal(attributes.get("muted"), "");
      this.playCalls++;
      this.paused = false;
      return { catch() {} };
    },
  };
  return video;
}

test("an autoplay request starts the broadcast muted", () => {
  const content = makeContent();
  const video = makeVideo();
  let clicks = 0;
  content.page.video = video;
  content.page.buttons = [{
    textContent: "재생하기",
    getAttribute() { return ""; },
    click() {
      assert.equal(video.muted, true);
      clicks++;
    },
  }];

  content.send({ type: "attempt-autoplay" });

  assert.equal(video.playCalls, 1);
  assert.equal(clicks, 1);
});

test("autoplay retries when the CHZZK player appears late", () => {
  const content = makeContent();
  content.send({ type: "attempt-autoplay" });
  const retryTimer = content.intervals.find((timer) => timer.delay === 1_000);
  assert.ok(retryTimer);

  const video = makeVideo();
  content.page.video = video;
  retryTimer.callback();

  assert.equal(video.playCalls, 1);
  assert.equal(content.timeouts.some((timer) => timer.delay === 30_000), true);
});

test("duplicate autoplay messages do not start duplicate retry loops", () => {
  const content = makeContent();
  content.send({ type: "attempt-autoplay" });
  content.send({ type: "attempt-autoplay" });

  assert.equal(content.intervals.filter((timer) => timer.delay === 1_000).length, 1);
  assert.equal(content.timeouts.filter((timer) => timer.delay === 30_000).length, 1);
});

test("autoplay can be requested again after the previous retry window ends", () => {
  const content = makeContent();
  content.send({ type: "attempt-autoplay" });
  const firstRetryTimer = content.intervals.find((timer) => timer.delay === 1_000);
  const firstStopTimer = content.timeouts.find((timer) => timer.delay === 30_000);

  firstStopTimer.callback();
  content.send({ type: "attempt-autoplay" });

  assert.equal(firstRetryTimer.cleared, true);
  assert.equal(content.intervals.filter((timer) => timer.delay === 1_000).length, 2);
  assert.equal(content.timeouts.filter((timer) => timer.delay === 30_000).length, 2);
});
