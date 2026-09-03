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

let autoplayRequested = false;

function tryAutoplay() {
  const video = document.querySelector("video");
  if (!video || !video.paused) return;

  const playButton = [...document.querySelectorAll("button")].find((button) => {
    const label = button.getAttribute("aria-label") || button.getAttribute("title") || "";
    return /^(재생|play)$/i.test(label.trim());
  });
  playButton?.click();
  const playRequest = video.play();
  playRequest?.catch(() => {
    // Chrome can block audible background autoplay. Retrying after the player
    // finishes loading is still useful for sites that allow it.
  });
}

function requestAutoplay() {
  if (autoplayRequested) return;
  autoplayRequested = true;
  for (const delay of [0, 700, 1_800, 3_500, 6_000]) {
    setTimeout(tryAutoplay, delay);
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "attempt-autoplay") requestAutoplay();
});
