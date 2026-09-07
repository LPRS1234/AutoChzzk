const LIVE_URL_PATTERN = /^https:\/\/chzzk\.naver\.com\/live\/([0-9a-f]{32})(?:[/?#]|$)/i;
const ENDPOINT = "http://127.0.0.1:8765/chzzk-tabs";
const completedCommandIds = new Set();
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
  for (const command of commands) {
    if (!command?.id || !command?.url || completedCommandIds.has(command.id)) continue;
    if (command.action === "close") {
      await closeAutoOpenedTabs(command.url);
      completedCommandIds.add(command.id);
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
  }
}

async function reportOpenChzzkLives() {
  if (reporting) return;
  reporting = true;
  try {
    const tabs = await chrome.tabs.query({ url: ["https://chzzk.naver.com/live/*"] });
    const channelIds = tabs
      .map((tab) => tab.url?.match(LIVE_URL_PATTERN)?.[1]?.toLowerCase())
      .filter(Boolean);
    const [clientId, focused, profileIdentity] = await Promise.all([getClientId(), isProfileFocused(), getProfileIdentity()]);
    const response = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientId, focused, ...profileIdentity, channelIds, completedCommandIds: [...completedCommandIds] }),
    });
    if (response.ok) await executeOpenCommands((await response.json()).openCommands || []);
  } catch {
    // AutoChzzk is not running yet. The next poll will reconnect automatically.
  } finally {
    reporting = false;
  }
}

chrome.runtime.onInstalled.addListener(async () => {
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
