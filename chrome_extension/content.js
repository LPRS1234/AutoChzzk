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
let autoplayRetryTimer = null;
let autoplayStopTimer = null;

const AUTOPLAY_RETRY_INTERVAL_MS = 1_000;
const AUTOPLAY_RETRY_DURATION_MS = 30_000;

function tryAutoplay() {
  const video = document.querySelector("video");
  if (!video || !video.paused) return;

  // Chrome always permits muted autoplay, including in a background tab.
  // Keep all three values aligned because the CHZZK player may inspect either
  // the DOM attribute or the current/default media properties while mounting.
  video.muted = true;
  video.defaultMuted = true;
  video.autoplay = true;
  video.setAttribute("muted", "");

  const playButton = [...document.querySelectorAll("button")].find((button) => {
    const label = button.getAttribute("aria-label") || button.getAttribute("title") || button.textContent || "";
    return /^(재생|재생하기|play)$/i.test(label.trim());
  });
  playButton?.click();
  const playRequest = video.play();
  playRequest?.catch(() => {
    // The player may not have attached its stream yet. The timer below retries
    // while the CHZZK page finishes mounting.
  });
}

function stopAutoplayRetries() {
  if (autoplayRetryTimer !== null) clearInterval(autoplayRetryTimer);
  if (autoplayStopTimer !== null) clearTimeout(autoplayStopTimer);
  autoplayRetryTimer = null;
  autoplayStopTimer = null;
  autoplayRequested = false;
}

function requestAutoplay() {
  if (autoplayRequested) return;
  autoplayRequested = true;
  autoplayRetryTimer = setInterval(tryAutoplay, AUTOPLAY_RETRY_INTERVAL_MS);
  autoplayStopTimer = setTimeout(stopAutoplayRetries, AUTOPLAY_RETRY_DURATION_MS);
  tryAutoplay();
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "attempt-autoplay") requestAutoplay();
});
