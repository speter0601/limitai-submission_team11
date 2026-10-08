(() => {
  const announce = () => window.postMessage({ source: "focus-ext", type: "ready" }, "*");

  window.addEventListener("message", (e) => {
    if (e.source !== window || e.data?.source !== "focus-page") return;
    const { type, session_id, query, sameTab } = e.data;
    if (type === "ping") announce();
    if (type === "open") chrome.runtime.sendMessage({ type: "open-search", session_id, query, sameTab });
    if (type === "end") chrome.runtime.sendMessage({ type: "end-session", session_id });
  });

  announce();
})();
