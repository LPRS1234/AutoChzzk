// The extension worker receives this lightweight heartbeat while Chrome runs.
setInterval(() => chrome.runtime.sendMessage({ type: "poll" }), 2000);
chrome.runtime.sendMessage({ type: "poll" });
