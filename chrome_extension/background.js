const LIVE_URL_PATTERN = /^https:\/\/chzzk\.naver\.com\/live\/([0-9a-f]{32})(?:[/?#]|$)/i;
const ENDPOINT = "http://127.0.0.1:8765/chzzk-tabs";
const completedCommandIds = new Set();
let reporting = false;

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
    const existing = await chrome.tabs.query({ url: [command.url] });
    if (existing.length === 0) {
      // active:false keeps the current app/window in front of Chrome.
      await chrome.tabs.create({ url: command.url, active: false });
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
    const response = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channelIds, completedCommandIds: [...completedCommandIds] }),
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
chrome.runtime.onMessage.addListener((message) => { if (message?.type === "poll") reportOpenChzzkLives(); });
chrome.tabs.onUpdated.addListener(reportOpenChzzkLives);
chrome.tabs.onRemoved.addListener(reportOpenChzzkLives);
chrome.tabs.onActivated.addListener(reportOpenChzzkLives);
chrome.windows.onFocusChanged.addListener(reportOpenChzzkLives);
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "report-open-chzzk-lives") reportOpenChzzkLives(); });
