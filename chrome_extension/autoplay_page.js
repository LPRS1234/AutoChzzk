// Runs in CHZZK's main JavaScript world only for tabs opened by AutoChzzk.
// CHZZK defers its player while a new tab reports itself as hidden. Report a
// visible/focused page and notify existing listeners without selecting the tab.
(() => {
  for (const [name, value] of [
    ["hidden", false],
    ["webkitHidden", false],
    ["visibilityState", "visible"],
    ["webkitVisibilityState", "visible"],
  ]) {
    try {
      Object.defineProperty(document, name, { configurable: true, get: () => value });
    } catch {
      // A future CHZZK/Chrome change may make an individual property fixed.
    }
  }

  try {
    Object.defineProperty(document, "hasFocus", { configurable: true, value: () => true });
  } catch {
    // The visibility signal and direct video.play() request can still work.
  }

  try {
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  } catch {
    // A direct muted playback request follows from the isolated content script.
  }
})();
