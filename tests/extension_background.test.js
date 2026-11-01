const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { createHmac, webcrypto } = require("node:crypto");
const vm = require("node:vm");

const source = readFileSync(new URL("../chrome_extension/background.js", `file://${__filename.replaceAll("\\", "/")}`), "utf8");
const secret = "ab".repeat(32);
const url = "https://chzzk.naver.com/live/" + "a".repeat(32);
const command = (number, action = "open") => ({ id: number.toString(16).padStart(32, "0"), action, url });
const reloadCommand = (number, version) => ({ id: number.toString(16).padStart(32, "0"), action: "reload", version });

function makeWorker({ storage = {}, localStorage = { pairingSecret: secret, clientId: "synthetic-client" }, version = "2.0.0", fetchImpl, timerImpl } = {}) {
  const calls = { identity: 0, created: 0, createdTabs: [], injections: [], removed: [], requests: [], messages: [], restricted: false, reloads: 0 };
  const event = () => ({ addListener() {} });
  let now = Date.now();
  const chrome = {
    storage: {
      local: {
        async setAccessLevel() { calls.restricted = true; },
        async get(keys) {
          assert.equal(calls.restricted, true);
          if (typeof keys === "string") return { [keys]: structuredClone(localStorage[keys]) };
          const selected = {};
          for (const key of (Array.isArray(keys) ? keys : Object.keys(localStorage))) selected[key] = structuredClone(localStorage[key]);
          return selected;
        },
        async set(values) { Object.assign(localStorage, structuredClone(values)); },
        async remove(key) { delete localStorage[key]; },
      },
      session: {
        async get() { return structuredClone(storage); },
        async set(values) { Object.assign(storage, structuredClone(values)); },
        async remove(key) { delete storage[key]; },
      },
    },
    scripting: { async executeScript(injection) { calls.injections.push(injection); } },
    tabs: {
      async query() { return []; }, async create(options) { calls.created++; calls.createdTabs.push(options); return { id: calls.created }; },
      async remove(ids) { calls.removed.push(...ids); }, async sendMessage(tabId, message) { calls.messages.push({ tabId, message }); },
      onUpdated: event(), onRemoved: event(), onActivated: event(),
    },
    windows: { async getLastFocused() { return { focused: true }; }, onFocusChanged: event() },
    identity: { async getProfileUserInfo() { calls.identity++; return { id: "synthetic-id", email: "test@example.invalid" }; } },
    offscreen: { async createDocument() {} },
    alarms: { create() {}, onAlarm: event() },
    runtime: { getManifest() { return { version }; }, reload() { calls.reloads++; }, onInstalled: event(), onStartup: event(), onMessage: event() },
    action: { onClicked: event() },
  };
  const context = vm.createContext({ chrome, crypto: webcrypto, TextEncoder, TextDecoder, URL, AbortController,
    setTimeout: timerImpl || setTimeout, clearTimeout,
    Date: { now: () => now },
    fetch: async (endpoint, options) => {
      calls.requests.push({ endpoint: String(endpoint), options });
      if (fetchImpl) return fetchImpl(endpoint, options);
      const body = new URL(endpoint).pathname === "/challenge"
        ? { ok: true, protocol: 2 }
        : { ok: true, acknowledgedCommandIds: JSON.parse(options.body).completedCommandIds, openCommands: [] };
      return signedResponse(endpoint, options, body);
    },
  });
  vm.runInContext(source, context);
  return { context, calls, storage, localStorage, chrome, advance(ms) { now += ms; }, run(code) { return vm.runInContext(code, context); } };
}

function signedResponse(endpoint, options, payload) {
  const headers = options.headers;
  const path = new URL(endpoint).pathname;
  const expected = createHmac("sha256", Buffer.from(secret, "hex"))
    .update(`POST\n${path}\n${headers["X-AutoChzzk-Time"]}\n${headers["X-AutoChzzk-Nonce"]}\n${options.body}`).digest("hex");
  assert.equal(headers["X-AutoChzzk-Signature"], expected);
  assert.equal(options.body.includes(secret), false);
  assert.equal(options.redirect, "error");
  const body = JSON.stringify(payload);
  const proof = createHmac("sha256", Buffer.from(secret, "hex"))
    .update(`response\n${headers["X-AutoChzzk-Nonce"]}\n200\n${body}`).digest("hex");
  return new Response(body, { headers: { "X-AutoChzzk-Signature": proof } });
}

test("rejects fake server before querying identity or sending private state", async () => {
  const worker = makeWorker({ fetchImpl: async () => new Response('{"ok":true,"protocol":2}') });
  await worker.run("reportOpenChzzkLives()");
  assert.equal(worker.calls.identity, 0);
  assert.equal(worker.calls.requests.length, 1);
  assert.equal(worker.calls.requests[0].options.body, "{}");
  assert.equal(worker.run("reporting"), false);
});

test("mutual HMAC authenticates challenge before reporting profile state", async () => {
  const worker = makeWorker();
  await worker.run("reportOpenChzzkLives()");
  assert.equal(worker.calls.identity, 1);
  assert.equal(worker.calls.requests.length, 2);
  assert.equal(worker.calls.requests[0].options.body, "{}");
  assert.equal(JSON.parse(worker.calls.requests[1].options.body).profileGaiaId, "synthetic-id");
});

test("log power reads logged-in balances, caches for a minute, and reports only amounts", async () => {
  const channelId = 'a'.repeat(32);
  let amount = 12345;
  let apiCalls = 0;
  const worker = makeWorker({ fetchImpl: async (endpoint, options) => {
    if (String(endpoint).startsWith('https://api.chzzk.naver.com/')) {
      apiCalls++;
      assert.equal(String(endpoint), `https://api.chzzk.naver.com/service/v1/channels/${channelId}/log-power`);
      assert.equal(options.credentials, 'include');
      assert.equal(options.cache, 'no-store');
      assert.equal(options.method, 'GET');
      return new Response(JSON.stringify({ code: 200, content: { amount } }));
    }
    const path = new URL(endpoint).pathname;
    return signedResponse(endpoint, options, path === '/challenge' ? { ok: true, protocol: 2 }
      : { ok: true, acknowledgedCommandIds: [], openCommands: [], logPowerChannelIds: [channelId] });
  } });
  worker.context.channels = [channelId];
  await worker.run('refreshLogPowers(channels)');
  await worker.run('reportOpenChzzkLives()');
  assert.equal(JSON.parse(worker.calls.requests.at(-1).options.body).logPowers[channelId], 12345);
  assert.equal(apiCalls, 1);
  amount = 0;
  worker.advance(61000);
  await worker.run('refreshLogPowers(channels)');
  assert.equal(worker.run('reportedLogPowers()')[channelId], 0);
  assert.equal(apiCalls, 2);
  worker.advance(91000);
  assert.equal(worker.run('reportedLogPowers()')[channelId], undefined);
});

test("log power failures and malformed balances never become zero or retain a previous balance", async () => {
  let payload = { code: 200, content: { amount: 100 } };
  const worker = makeWorker({ fetchImpl: async () => {
    if (payload === null) throw new Error('offline');
    return new Response(JSON.stringify(payload));
  } });
  const channelId = 'b'.repeat(32);
  worker.context.channels = [channelId];
  for (const invalid of [null, { code: 401, content: null }, { code: 200, content: {} },
    { code: 200, content: { amount: '100' } }, { code: 200, content: { amount: -1 } },
    { code: 200, content: { amount: 2**53 } }]) {
    payload = { code: 200, content: { amount: 100 } };
    worker.advance(61000);
    await worker.run('refreshLogPowers(channels)');
    assert.equal(worker.run('reportedLogPowers()')[channelId], 100);
    payload = invalid;
    worker.advance(61000);
    await worker.run('refreshLogPowers(channels)');
    assert.equal(worker.run('reportedLogPowers()')[channelId], null);
  }
});

test("log power queries are bounded, deduplicated, and removed when no longer requested", async () => {
  let active = 0;
  let peak = 0;
  const queried = [];
  const worker = makeWorker({ fetchImpl: async (endpoint) => {
    queried.push(String(endpoint));
    peak = Math.max(peak, ++active);
    await new Promise(resolve => setImmediate(resolve));
    active--;
    return new Response('{"code":200,"content":{"amount":1}}');
  } });
  worker.context.channels = [...Array.from({ length: 12 }, (_, i) => i.toString(16).padStart(32, '0')),
    'bad', 'https://example.com', 'a'.repeat(32), 'a'.repeat(32)];
  await worker.run('Promise.all([refreshLogPowers(channels), refreshLogPowers(channels)])');
  assert.ok(peak <= 4);
  assert.equal(queried.length, 13);
  await worker.run('refreshLogPowers([])');
  assert.deepEqual(Object.keys(worker.run('reportedLogPowers()')), []);
});

test("a slow log power query does not block tab commands and discards results after deselection", async () => {
  let release;
  let reports = 0;
  const worker = makeWorker({ fetchImpl: async (endpoint, options) => {
    if (String(endpoint).startsWith('https://api.chzzk.naver.com/')) {
      await new Promise(resolve => { release = resolve; });
      return new Response('{"code":200,"content":{"amount":12345}}');
    }
    const path = new URL(endpoint).pathname;
    return signedResponse(endpoint, options, path === '/challenge' ? { ok: true, protocol: 2 }
      : { ok: true, acknowledgedCommandIds: [], openCommands: ++reports === 2 ? [command(88)] : [],
        logPowerChannelIds: reports === 1 ? ['a'.repeat(32)] : [] });
  } });
  try {
    await worker.run('reportOpenChzzkLives()');
    assert.equal(typeof release, 'function');
    await worker.run('reportOpenChzzkLives()');
    assert.equal(worker.calls.created, 1);
    assert.equal(worker.run('reporting'), false);
  } finally {
    release?.();
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.deepEqual(Object.keys(worker.run('reportedLogPowers()')), []);
});

test("rejects tampered response and never executes its command", async () => {
  const worker = makeWorker({ fetchImpl: async (endpoint, options) => {
    const signed = signedResponse(endpoint, options, { ok: true, protocol: 2 });
    return new Response(JSON.stringify({ ok: true, protocol: 2, openCommands: [command(1)] }), { headers: signed.headers });
  } });
  await worker.run("reportOpenChzzkLives()");
  assert.equal(worker.calls.identity, 0);
  assert.equal(worker.calls.created, 0);
});

test("only exact broadcast URLs and validated commands reach Chrome", async () => {
  const worker = makeWorker();
  worker.context.commands = [null, { ...command(1), url: "https://example.com" },
    { ...command(2), url: url + "?next=evil" }, { ...command(3), id: 3 }, command(4), command(4)];
  await worker.run("executeOpenCommands(commands)");
  assert.equal(worker.calls.created, 1);
});

test("an automatic broadcast open keeps the user's current tab selected", async () => {
  const worker = makeWorker();
  worker.context.commands = [command(1)];

  await worker.run("executeOpenCommands(commands)");

  assert.equal(worker.calls.createdTabs.length, 1);
  assert.equal(worker.calls.createdTabs[0].url, url);
  assert.equal(worker.calls.createdTabs[0].active, false);
});

test("thousands of acknowledged commands keep request and dedup state bounded", async () => {
  const worker = makeWorker();
  for (let batch = 0; batch < 20; batch++) {
    worker.context.commands = Array.from({ length: 128 }, (_, i) => command(batch * 128 + i + 1));
    await worker.run("executeOpenCommands(commands)");
    assert.equal(worker.run("completedCommandIds.size"), 128);
    await worker.run("reportOpenChzzkLives()");
    assert.equal(worker.run("completedCommandIds.size"), 0);
    assert.ok(worker.run("recentCommandIds.size") <= 1024);
    assert.ok(Buffer.byteLength(worker.calls.requests.at(-1).options.body) < 32768);
    worker.advance(121000);
  }
  assert.equal(worker.calls.created, 2560);
});

test("dedup survives service worker restart after acknowledgement", async () => {
  const storage = {};
  const first = makeWorker({ storage });
  first.context.commands = [command(1)];
  await first.run("executeOpenCommands(commands)");
  await first.run("reportOpenChzzkLives()");
  const restarted = makeWorker({ storage });
  restarted.context.commands = [command(1)];
  await restarted.run("executeOpenCommands(commands)");
  assert.equal(restarted.calls.created, 0);
  assert.equal(restarted.run("completedCommandIds.size"), 1);
});

test("timed out fetch releases reporting guard for next poll", async () => {
  const worker = makeWorker({
    timerImpl: (callback) => setTimeout(callback, 5),
    fetchImpl: (_endpoint, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    }),
  });
  await worker.run("reportOpenChzzkLives()");
  assert.equal(worker.run("reporting"), false);
  await worker.run("reportOpenChzzkLives()");
  assert.equal(worker.calls.requests.length, 2);
});

test("close removes only app-opened tabs", async () => {
  const worker = makeWorker({ storage: { autoOpenedTabIds: [10] } });
  worker.chrome.tabs.query = async () => [{ id: 10 }, { id: 11 }];
  worker.context.commands = [command(1, "close")];
  await worker.run("executeOpenCommands(commands)");
  assert.deepEqual(worker.calls.removed, [10]);
});

test("autoplay request survives a service worker restart for an app-opened tab", async () => {
  const worker = makeWorker({ storage: { autoOpenedTabIds: [10] } });

  await worker.run("requestAutoplayIfAutoOpened(10)");
  await worker.run("requestAutoplayIfAutoOpened(11)");

  assert.equal(worker.calls.messages.length, 1);
  assert.equal(worker.calls.messages[0].tabId, 10);
  assert.equal(worker.calls.messages[0].message.type, "attempt-autoplay");
  assert.equal(worker.calls.injections.length, 1);
  assert.equal(worker.calls.injections[0].target.tabId, 10);
  assert.equal(worker.calls.injections[0].files[0], "autoplay_page.js");
  assert.equal(worker.calls.injections[0].world, "MAIN");
  assert.equal(worker.calls.injections[0].injectImmediately, true);
});

test("reload applies updated files once and restores app-opened tab ownership", async () => {
  const localStorage = { pairingSecret: secret, clientId: "synthetic-client" };
  const firstSession = { autoOpenedTabIds: [10] };
  const first = makeWorker({ storage: firstSession, localStorage, version: "2.1.0" });
  first.chrome.tabs.query = async () => [{ id: 10, url }];
  first.context.commands = [reloadCommand(1, "2.2.0")];
  await first.run("executeOpenCommands(commands)");
  assert.equal(first.calls.reloads, 1);
  assert.equal(localStorage.extensionReloadAttempt.targetVersion, "2.2.0");
  assert.equal(localStorage.extensionReloadHandoff.commandId, reloadCommand(1, "2.2.0").id);

  const restarted = makeWorker({ storage: {}, localStorage, version: "2.2.0" });
  restarted.chrome.tabs.query = async () => [{ id: 10, url }];
  await restarted.run("shouldRestoreReloadHandoff = true; restoreReloadHandoff()")
  restarted.context.commands = [reloadCommand(1, "2.2.0")];
  await restarted.run("executeOpenCommands(commands)");
  assert.equal(restarted.calls.reloads, 0);
  assert.deepEqual(restarted.storage.autoOpenedTabIds, [10]);
  assert.equal(restarted.run("completedCommandIds.size"), 1);
  assert.equal(localStorage.extensionReloadHandoff, undefined);
  assert.equal(localStorage.extensionReloadAttempt.targetVersion, "2.2.0");
});

test("browser startup does not restore a reload-only session handoff", async () => {
  const commandId = reloadCommand(1, "2.2.0").id;
  const localStorage = {
    pairingSecret: secret,
    clientId: "synthetic-client",
    extensionReloadHandoff: { commandId, targetVersion: "2.2.0", createdAt: Date.now(),
      completedCommandIds: [], recentCommandIds: [], autoOpenedTabs: [{ id: 10, url }] },
  };
  const worker = makeWorker({ storage: {}, localStorage, version: "2.2.0" });
  worker.chrome.tabs.query = async () => [{ id: 10, url }];

  await worker.run("reportOpenChzzkLives()");

  assert.equal(worker.storage.autoOpenedTabIds, undefined);
});

test("a previous attempt blocks another reload for the same target version", async () => {
  const localStorage = { pairingSecret: secret, clientId: "synthetic-client",
    extensionReloadAttempt: { commandId: reloadCommand(1, "2.2.0").id, targetVersion: "2.2.0", attemptedAt: Date.now() } };
  const worker = makeWorker({ localStorage, version: "2.1.0" });
  worker.context.commands = [reloadCommand(2, "2.2.0")];

  await worker.run("executeOpenCommands(commands)");

  assert.equal(worker.calls.reloads, 0);
  assert.equal(worker.run("completedCommandIds.size"), 1);
});

test("handoff restoration resumes after an update worker restart", async () => {
  const commandId = reloadCommand(1, "2.2.0").id;
  const localStorage = { pairingSecret: secret, clientId: "synthetic-client",
    extensionReloadHandoff: { commandId, targetVersion: "2.2.0", createdAt: Date.now(),
      completedCommandIds: [], recentCommandIds: [], autoOpenedTabs: [{ id: 10, url }] } };
  const storage = { reloadHandoffRestorePending: true };
  const worker = makeWorker({ storage, localStorage, version: "2.2.0" });
  worker.chrome.tabs.query = async () => [{ id: 10, url }];

  await worker.run("reportOpenChzzkLives()");

  assert.deepEqual(storage.autoOpenedTabIds, [10]);
  assert.equal(storage.reloadHandoffRestorePending, undefined);
  assert.equal(localStorage.extensionReloadHandoff, undefined);
});
