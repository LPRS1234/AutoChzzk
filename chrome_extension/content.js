// Runs inside each CHZZK live page and wakes the service worker while the tab exists.
const heartbeat = () => chrome.runtime.sendMessage({ type: "live-tab-heartbeat" }).catch(() => {});
heartbeat();
setInterval(heartbeat, 2000);
