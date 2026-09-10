const LIVE_URL_PATTERN = /^https:\/\/chzzk\.naver\.com\/live\/([0-9a-f]{32})(?:[/?#]|$)/i;
const ENDPOINT = "http://127.0.0.1:8765/chzzk-tabs";
const completedCommandIds = new Set();
const recentCommandIds = new Map();
const COMMAND_URL_PATTERN = /^https:\/\/chzzk\.naver\.com\/live\/[0-9a-f]{32}$/i;
const VERSION_PATTERN = /^\d+(?:\.\d+){2,3}$/;
const RELOAD_ATTEMPT_KEY = "extensionReloadAttempt";
const RELOAD_HANDOFF_KEY = "extensionReloadHandoff";
const RELOAD_RESTORE_PENDING_KEY = "reloadHandoffRestorePending";
const RELOAD_HANDOFF_TTL = 120000;
const MAX_PRESERVED_TABS = 1024;
const encoder = new TextEncoder();
const storageReady = chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
let commandStateLoaded = false;
let reloadHandoffLoaded = false;
let shouldRestoreReloadHandoff = false;

async function loadCommandState() {
  if (commandStateLoaded) return;
  const state = await chrome.storage.session.get(["completedCommandIds", "recentCommandIds"]);
  for (const id of state.completedCommandIds || []) completedCommandIds.add(id);
  for (const [id, time] of state.recentCommandIds || []) recentCommandIds.set(id, time);
  commandStateLoaded = true;
}

async function saveCommandState() {
  await chrome.storage.session.set({ completedCommandIds: [...completedCommandIds], recentCommandIds: [...recentCommandIds] });
}

function compareVersions(left, right) {
  const leftParts = left.split(".").map(Number);
  const rightParts = right.split(".").map(Number);
  const length = Math.max(leftParts.length, rightParts.length);
  for (let index = 0; index < length; index++) {
    const difference = (leftParts[index] || 0) - (rightParts[index] || 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

async function restoreReloadHandoff() {
  if (reloadHandoffLoaded) return;
  await storageReady;
  const stored = (await chrome.storage.local.get(RELOAD_HANDOFF_KEY))[RELOAD_HANDOFF_KEY];
  const now = Date.now();
  if (!stored || typeof stored !== "object" || typeof stored.commandId !== "string"
      || !/^[0-9a-f]{32}$/.test(stored.commandId) || typeof stored.targetVersion !== "string"
      || !VERSION_PATTERN.test(stored.targetVersion) || !Number.isFinite(stored.createdAt)
      || stored.createdAt > now || now - stored.createdAt > RELOAD_HANDOFF_TTL) {
    await chrome.storage.local.remove(RELOAD_HANDOFF_KEY);
    await chrome.storage.session.remove(RELOAD_RESTORE_PENDING_KEY);
    reloadHandoffLoaded = true;
    return;
  }
  await loadCommandState();
  for (const id of (Array.isArray(stored.completedCommandIds) ? stored.completedCommandIds : [])) {
    if (typeof id === "string" && /^[0-9a-f]{32}$/.test(id) && completedCommandIds.size < 128) completedCommandIds.add(id);
  }
  for (const entry of (Array.isArray(stored.recentCommandIds) ? stored.recentCommandIds : [])) {
    if (!Array.isArray(entry) || entry.length !== 2) continue;
    const [id, created] = entry;
    if (typeof id === "string" && /^[0-9a-f]{32}$/.test(id) && Number.isFinite(created)
        && created <= now && now - created <= RELOAD_HANDOFF_TTL
        && recentCommandIds.size < 1024) recentCommandIds.set(id, created);
  }
  const tabs = await chrome.tabs.query({ url: ["https://chzzk.naver.com/live/*"] });
  const validTabs = new Map(tabs.filter((tab) => typeof tab.id === "number" && typeof tab.url === "string")
    .map((tab) => [tab.id, tab.url]));
  const autoOpenedTabIds = await getAutoOpenedTabIds();
  for (const tab of (Array.isArray(stored.autoOpenedTabs) ? stored.autoOpenedTabs : []).slice(0, MAX_PRESERVED_TABS)) {
    if (typeof tab?.id === "number" && typeof tab.url === "string" && COMMAND_URL_PATTERN.test(tab.url)
        && validTabs.get(tab.id) === tab.url) autoOpenedTabIds.add(tab.id);
  }
  await chrome.storage.session.set({ autoOpenedTabIds: [...autoOpenedTabIds] });
  await saveCommandState();
  await chrome.storage.local.remove(RELOAD_HANDOFF_KEY);
  await chrome.storage.session.remove(RELOAD_RESTORE_PENDING_KEY);
  reloadHandoffLoaded = true;
}

async function getPairingKey() {
  await storageReady;
  const { pairingSecret } = await chrome.storage.local.get("pairingSecret");
  if (typeof pairingSecret !== "string" || !/^[0-9a-f]{64}$/.test(pairingSecret)) throw new Error("Pairing required");
  return crypto.subtle.importKey("raw", Uint8Array.from(pairingSecret.match(/../g), (part) => parseInt(part, 16)), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

async function authenticatedPost(path, payload, key) {
  const nonce = crypto.randomUUID().replaceAll("-", "");
  const timestamp = String(Math.floor(Date.now() / 1000));
  const body = JSON.stringify(payload);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(`POST\n${path}\n${timestamp}\n${nonce}\n${body}`));
  const signatureHex = [...new Uint8Array(signature)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch(new URL(path, ENDPOINT), {
      method: "POST", redirect: "error", cache: "no-store", signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-AutoChzzk-Time": timestamp, "X-AutoChzzk-Nonce": nonce, "X-AutoChzzk-Signature": signatureHex }, body,
    });
    if (!response.ok) throw new Error("Bridge rejected request");
    const proof = response.headers.get("X-AutoChzzk-Signature") || "";
    if (!/^[0-9a-f]{64}$/.test(proof)) throw new Error("Unauthenticated bridge");
    // Limit a malicious local server's response before allocating an unbounded string.
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 32768) { await reader.cancel(); throw new Error("Response too large"); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const prefix = encoder.encode(`response\n${nonce}\n${response.status}\n`);
    const signed = new Uint8Array(prefix.length + size);
    signed.set(prefix); signed.set(bytes, prefix.length);
    const valid = await crypto.subtle.verify("HMAC", key, Uint8Array.from(proof.match(/../g), (part) => parseInt(part, 16)), signed);
    if (!valid) throw new Error("Unauthenticated bridge");
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } finally {
    clearTimeout(timer);
  }
}
const autoPlayTabIds = new Set();
let reporting = false;

function requestAutoplay(tabId) {
  chrome.tabs.sendMessage(tabId, { type: "attempt-autoplay" }).catch(() => {});
}

async function getAutoOpenedTabIds() {
  const { autoOpenedTabIds = [] } = await chrome.storage.session.get("autoOpenedTabIds");
  return new Set(autoOpenedTabIds.filter(Number.isInteger));
}

async function rememberAutoOpenedTab(tabId) {
  const tabIds = await getAutoOpenedTabIds();
  tabIds.add(tabId);
  await chrome.storage.session.set({ autoOpenedTabIds: [...tabIds] });
}

async function forgetAutoOpenedTab(tabId) {
  const tabIds = await getAutoOpenedTabIds();
  if (!tabIds.delete(tabId)) return;
  await chrome.storage.session.set({ autoOpenedTabIds: [...tabIds] });
}

async function closeAutoOpenedTabs(url) {
  const [tabs, autoOpenedTabIds] = await Promise.all([
    chrome.tabs.query({ url: [url] }),
    getAutoOpenedTabIds(),
  ]);
  const tabIds = tabs
    .map((tab) => tab.id)
    .filter((tabId) => typeof tabId === "number" && autoOpenedTabIds.has(tabId));
  if (tabIds.length === 0) return;
  await chrome.tabs.remove(tabIds);
  for (const tabId of tabIds) autoOpenedTabIds.delete(tabId);
  await chrome.storage.session.set({ autoOpenedTabIds: [...autoOpenedTabIds] });
}

async function getClientId() {
  const { clientId } = await chrome.storage.local.get("clientId");
  if (typeof clientId === "string" && clientId.length >= 8) return clientId;
  const newClientId = crypto.randomUUID();
  await chrome.storage.local.set({ clientId: newClientId });
  return newClientId;
}

async function isProfileFocused() {
  try {
    return Boolean((await chrome.windows.getLastFocused()).focused);
  } catch {
    return false;
  }
}

async function getProfileIdentity() {
  try {
    const profile = await chrome.identity.getProfileUserInfo({ accountStatus: "ANY" });
    return { profileGaiaId: profile.id || "", profileEmail: profile.email || "" };
  } catch {
    return { profileGaiaId: "", profileEmail: "" };
  }
}

async function ensurePoller() {
  try {
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["WORKERS"],
      justification: "Keep the local AutoChzzk companion connection responsive.",
    });
  } catch {
    // The offscreen document already exists.
  }
}

async function executeOpenCommands(commands) {
  if (!Array.isArray(commands) || commands.length > 128) return;
  await loadCommandState();
  for (const [id, created] of recentCommandIds) {
    if (Date.now() - created > 120000 && !completedCommandIds.has(id)) recentCommandIds.delete(id);
  }
  for (const command of commands) {
    if (typeof command?.id !== "string" || !/^[0-9a-f]{32}$/.test(command.id)) continue;
    if (completedCommandIds.size >= 128) break;
    if (recentCommandIds.has(command.id)) { completedCommandIds.add(command.id); continue; }
    if (recentCommandIds.size >= 1024) break;
    if (command.action === "reload") {
      if (typeof command.version !== "string" || !VERSION_PATTERN.test(command.version)) continue;
      await storageReady;
      const currentVersion = chrome.runtime.getManifest().version;
      const attempt = (await chrome.storage.local.get(RELOAD_ATTEMPT_KEY))[RELOAD_ATTEMPT_KEY];
      if (compareVersions(currentVersion, command.version) >= 0 || attempt?.targetVersion === command.version) {
        completedCommandIds.add(command.id);
        recentCommandIds.set(command.id, Date.now());
        await saveCommandState();
        continue;
      }
      const [autoOpenedTabIds, tabs] = await Promise.all([
        getAutoOpenedTabIds(),
        chrome.tabs.query({ url: ["https://chzzk.naver.com/live/*"] }),
      ]);
      const autoOpenedTabs = tabs.filter((tab) => typeof tab.id === "number" && autoOpenedTabIds.has(tab.id)
        && typeof tab.url === "string" && COMMAND_URL_PATTERN.test(tab.url));
      if (autoOpenedTabs.length > MAX_PRESERVED_TABS) return;
      const handoff = {
        commandId: command.id,
        targetVersion: command.version,
        createdAt: Date.now(),
        completedCommandIds: [...completedCommandIds],
        recentCommandIds: [...recentCommandIds],
        autoOpenedTabs: autoOpenedTabs.map((tab) => ({ id: tab.id, url: tab.url })),
      };
      await chrome.storage.local.set({
        [RELOAD_ATTEMPT_KEY]: { commandId: command.id, targetVersion: command.version, attemptedAt: Date.now() },
        [RELOAD_HANDOFF_KEY]: handoff,
      });
      chrome.runtime.reload();
      return;
    }
    if (typeof command.url !== "string" || !COMMAND_URL_PATTERN.test(command.url)
        || !["open", "close"].includes(command.action)) continue;
    if (command.action === "close") {
      await closeAutoOpenedTabs(command.url);
      completedCommandIds.add(command.id);
      recentCommandIds.set(command.id, Date.now());
      await saveCommandState();
      continue;
    }
    if (command.action !== "open") continue;
    const existing = await chrome.tabs.query({ url: [command.url] });
    if (existing.length === 0) {
      // active:false keeps the current app/window in front of Chrome.
      const tab = await chrome.tabs.create({ url: command.url, active: false });
      if (typeof tab.id === "number") {
        autoPlayTabIds.add(tab.id);
        await rememberAutoOpenedTab(tab.id);
        requestAutoplay(tab.id);
      }
    }
    completedCommandIds.add(command.id);
    recentCommandIds.set(command.id, Date.now());
    await saveCommandState();
  }
}

async function reportOpenChzzkLives() {
  if (reporting) return;
  reporting = true;
  try {
    const pendingRestore = (await chrome.storage.session.get(RELOAD_RESTORE_PENDING_KEY))[RELOAD_RESTORE_PENDING_KEY] === true;
    if (shouldRestoreReloadHandoff || pendingRestore) await restoreReloadHandoff();
    const key = await getPairingKey();
    // Prove the local server knows the paired key before looking up/sending profile data.
    const challenge = await authenticatedPost("/challenge", {}, key);
    if (challenge?.protocol !== 2 || challenge?.ok !== true) throw new Error("Incompatible bridge");
    await loadCommandState();
    const tabs = await chrome.tabs.query({ url: ["https://chzzk.naver.com/live/*"] });
    const channelIds = tabs
      .map((tab) => tab.url?.match(LIVE_URL_PATTERN)?.[1]?.toLowerCase())
      .filter(Boolean);
    const [clientId, focused, profileIdentity] = await Promise.all([getClientId(), isProfileFocused(), getProfileIdentity()]);
    const extensionVersion = chrome.runtime.getManifest().version;
    const sentIds = [...completedCommandIds].slice(0, 128);
    const response = await authenticatedPost("/chzzk-tabs", { clientId, focused, ...profileIdentity, extensionVersion,
      channelIds: [...new Set(channelIds)].slice(0, 256), completedCommandIds: sentIds }, key);
    if (response?.ok !== true || !Array.isArray(response.acknowledgedCommandIds)) return;
    for (const id of response.acknowledgedCommandIds) {
      if (sentIds.includes(id)) completedCommandIds.delete(id);
    }
    await saveCommandState();
    await executeOpenCommands(response.openCommands);

  } catch {
    // AutoChzzk is not running yet. The next poll will reconnect automatically.
  } finally {
    reporting = false;
  }
}

chrome.runtime.onInstalled.addListener(async (details) => {
  shouldRestoreReloadHandoff = details?.reason === "update";
  if (shouldRestoreReloadHandoff) {
    await chrome.storage.session.set({ [RELOAD_RESTORE_PENDING_KEY]: true });
    try {
      await restoreReloadHandoff();
    } catch {
      // A later authenticated poll retries the one-time handoff restoration.
    }
  }
  await ensurePoller();
  chrome.alarms.create("report-open-chzzk-lives", { periodInMinutes: 0.5 });
  reportOpenChzzkLives();
});
chrome.runtime.onStartup.addListener(async () => { await ensurePoller(); reportOpenChzzkLives(); });
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "poll") reportOpenChzzkLives();
});
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  reportOpenChzzkLives();
  if (autoPlayTabIds.has(tabId) && changeInfo.status === "complete") {
    autoPlayTabIds.delete(tabId);
    requestAutoplay(tabId);
  }
});
chrome.tabs.onRemoved.addListener((tabId) => {
  forgetAutoOpenedTab(tabId).catch(() => {});
  reportOpenChzzkLives();
});
chrome.tabs.onActivated.addListener(reportOpenChzzkLives);
chrome.windows.onFocusChanged.addListener(reportOpenChzzkLives);
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "report-open-chzzk-lives") reportOpenChzzkLives(); });

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
