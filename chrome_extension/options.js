document.getElementById("pairing-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("pairing-code");
  const status = document.getElementById("status");
  const pairingSecret = input.value.trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(pairingSecret)) {
    status.textContent = "앱에서 복사한 64자리 연결 코드를 입력해 주세요.";
    return;
  }
  try {
    await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
    await chrome.storage.local.set({ pairingSecret });
    input.value = "";
    status.textContent = "저장했습니다. 앱을 실행하면 연결을 확인합니다. 앱의 확장 연결 상태를 확인하세요.";
    await chrome.runtime.sendMessage({ type: "poll" });
  } catch {
    status.textContent = "연결 코드를 저장하지 못했습니다. 확장 프로그램을 새로고침한 뒤 다시 시도해 주세요.";
  }
});
