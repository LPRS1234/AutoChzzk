const LIVE_URL_PATTERN = /^https:\/\/chzzk\.naver\.com\/live\/([0-9a-f]{32})(?:[/?#]|$)/i;
const ENDPOINT = "http://127.0.0.1:8765/chzzk-tabs";

async function reportOpenChzzkLives() {
  const tabs = await chrome.tabs.query({ url: ["https://chzzk.naver.com/live/*"] });
  const channelIds = tabs
    .map((tab) => tab.url?.match(LIVE_URL_PATTERN)?.[1]?.toLowerCase())
    .filter(Boolean);

  try {
    await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channelIds }),
    });
  } catch {
    // AutoChzzk is not running yet. The next report will reconnect automatically.
  }
}

chrome.runtime.onInstalled.addListener(() => {
  // Chrome guarantees alarms at a minimum of 0.5 minutes (30 seconds).
  chrome.alarms.create("report-open-chzzk-lives", { periodInMinutes: 0.5 });
  reportOpenChzzkLives();
});
chrome.runtime.onStartup.addListener(reportOpenChzzkLives);
chrome.tabs.onUpdated.addListener(reportOpenChzzkLives);
chrome.tabs.onRemoved.addListener(reportOpenChzzkLives);
chrome.tabs.onActivated.addListener(reportOpenChzzkLives);
chrome.windows.onFocusChanged.addListener(reportOpenChzzkLives);
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "report-open-chzzk-lives") reportOpenChzzkLives();
});
