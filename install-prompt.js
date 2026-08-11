(() => {
  const STORAGE_KEY = "hornsby-home-screen-prompt-v1";
  const PROMPT_DELAY_MS = 2500;
  const userAgent = navigator.userAgent;
  const isIOS =
    /iPad|iPhone|iPod/.test(userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isChrome =
    /CriOS/.test(userAgent) ||
    (/Chrome|Chromium/.test(userAgent) &&
      !/EdgA|EdgiOS|OPR|SamsungBrowser/.test(userAgent));
  const isSafari =
    /Version\/[\d.]+.*Safari/.test(userAgent) &&
    !/CriOS|Chrome|Chromium|EdgA|EdgiOS|OPR|OPiOS|FxiOS/.test(userAgent);
  const isMobile =
    /Android|iPad|iPhone|iPod|Mobile/.test(userAgent) ||
    (navigator.maxTouchPoints > 1 && window.matchMedia("(pointer: coarse)").matches);
  const isInstalled =
    window.matchMedia("(display-mode: standalone)").matches ||
    navigator.standalone === true;

  if (!isMobile || (!isChrome && !isSafari) || isInstalled) return;

  try {
    if (localStorage.getItem(STORAGE_KEY)) return;
  } catch {
    // Continue when storage is unavailable, such as in a restricted private tab.
  }

  const prompt = document.querySelector("#home-screen-prompt");
  const title = document.querySelector("#home-screen-title");
  const copy = document.querySelector("#home-screen-copy");
  const steps = document.querySelector("#home-screen-steps");
  const addButton = document.querySelector("#home-screen-add");
  const dismissButton = document.querySelector("#home-screen-dismiss");
  const closeButton = document.querySelector("#home-screen-close");

  if (!prompt || !title || !copy || !steps || !addButton || !dismissButton || !closeButton) {
    return;
  }

  let installEvent = null;
  let hasShown = false;
  let showingInstructions = false;

  function rememberChoice(choice) {
    try {
      localStorage.setItem(STORAGE_KEY, choice);
    } catch {
      // The prompt still closes when storage is unavailable.
    }
  }

  function hidePrompt(choice) {
    if (choice) rememberChoice(choice);
    prompt.hidden = true;
    document.body.classList.remove("home-screen-prompt-open");
  }

  function showPrompt() {
    if (hasShown || document.visibilityState !== "visible") return;
    hasShown = true;
    prompt.hidden = false;
    document.body.classList.add("home-screen-prompt-open");
    addButton.focus({ preventScroll: true });
  }

  function showManualInstructions() {
    title.textContent = isChrome ? "Add to Home Screen in Chrome" : "Add to Home Screen in Safari";
    copy.textContent = "Follow these steps to keep Hornsby Chiropractor one tap away:";
    const instructions = isIOS
      ? ["Tap the Share button in the browser toolbar.", "Choose Add to Home Screen.", "Tap Add to confirm."]
      : ["Open the Chrome menu (⋮).", "Choose Add to home screen or Install app.", "Tap Install to confirm."];

    steps.replaceChildren(
      ...instructions.map((instruction) => {
        const item = document.createElement("li");
        item.textContent = instruction;
        return item;
      }),
    );
    steps.hidden = false;
    addButton.textContent = "Got it";
    dismissButton.hidden = true;
    showingInstructions = true;
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installEvent = event;
  });

  window.addEventListener("appinstalled", () => hidePrompt("installed"));

  addButton.addEventListener("click", async () => {
    if (showingInstructions) {
      hidePrompt("instructions-shown");
      return;
    }

    if (!installEvent) {
      showManualInstructions();
      return;
    }

    const currentInstallEvent = installEvent;
    installEvent = null;
    await currentInstallEvent.prompt();
    const { outcome } = await currentInstallEvent.userChoice;
    hidePrompt(outcome === "accepted" ? "installed" : "dismissed");
  });

  dismissButton.addEventListener("click", () => hidePrompt("dismissed"));
  closeButton.addEventListener("click", () => hidePrompt("dismissed"));
  prompt.addEventListener("click", (event) => {
    if (event.target === prompt) hidePrompt("dismissed");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !prompt.hidden) hidePrompt("dismissed");
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => null);
    });
  }

  window.setTimeout(showPrompt, PROMPT_DELAY_MS);
})();
