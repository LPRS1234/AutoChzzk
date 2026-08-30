// Runs inside each CHZZK live page and wakes the service worker while the tab exists.
// A tab can temporarily retain an old script after the extension is reloaded.
// In that case Chrome invalidates its runtime context before the tab refreshes.
const heartbeat = () => {
  try {
    if (!chrome.runtime?.id) return;
    const request = chrome.runtime.sendMessage({ type: "live-tab-heartbeat" });
    request?.catch(() => {});
  } catch {
    // The old extension context is gone; the refreshed script will reconnect.
  }
};
heartbeat();
setInterval(heartbeat, 2000);
