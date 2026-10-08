(() => {
  if (window.top !== window) return;
  const { api, el, sectionTitle } = window.Focus;

  function articleFrame() {
    const frame = document.querySelector("iframe#mainFrame");
    try {
      if (frame?.contentDocument?.body) return frame;
    } catch {
    }
    return null;
  }

  function canonicalUrl(loc) {
    if (/blog\.naver\.com$/.test(loc.hostname)) {
      const p = new URLSearchParams(loc.search);
      if (p.get("blogId") && p.get("logNo")) return `https://blog.naver.com/${p.get("blogId")}/${p.get("logNo")}`;
      const m = loc.pathname.match(/^\/([^/]+)\/(\d+)/);
      if (m) return `https://blog.naver.com/${m[1]}/${m[2]}`;
    }
    return loc.href.split("#")[0];
  }

  const BODY = ".se-main-container, #postViewArea, .post_ct, #viewTypeSelector, article, [role='main'], main, #content, .content";
  const TITLE = ".se-title-text, .se_title, .pcol1, .tit_h3, article h1, h1";
  function extractArticle() {
    const frame = articleFrame();
    const doc = frame ? frame.contentDocument : document;
    const loc = frame ? frame.contentWindow.location : location;
    const root = doc.querySelector(BODY) || doc.body;
    const text = (root?.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
    if (text.length < 100) return null;
    const titleNode = doc.querySelector(TITLE);
    return {
      url: canonicalUrl(loc),
      title: (titleNode?.textContent || document.title).replace(/\s+/g, " ").trim().slice(0, 200),
      text: text.slice(0, 6000),
    };
  }

  function pageTitle() {
    const article = extractArticle();
    return article?.title || document.title || location.hostname;
  }

  function renderSaved(panel, saved, cached) {
    const list = el("ul", "focus-summary");
    (saved.summary || []).forEach((line) => list.append(el("li", null, line)));
    const memo = el("textarea", "focus-memo");
    memo.placeholder = "메모 (자동 저장)";
    memo.value = saved.memo || "";
    memo.addEventListener("change", () =>
      api("/api/archive", { session_id: panel.sessionId, url: saved.url, memo: memo.value }, "PATCH")
    );
    panel.show(
      "page",
      sectionTitle("이 페이지", cached ? "담음 ✓ (저장된 요약)" : "담음 ✓"),
      el("div", "focus-intent-text", saved.title),
      list,
      el("div", "focus-step-query", (saved.keywords || []).map((k) => `#${k}`).join(" ")),
      memo
    );
  }

  function renderPageCard(panel) {
    const memo = el("textarea", "focus-memo");
    memo.placeholder = "왜 담는지 한 줄 메모 (선택)";
    const button = el("button", "focus-btn focus-primary focus-wide", "이 페이지 담기");
    const note = el("div", "focus-muted", "본문을 HCX-DASH-002가 3줄로 요약해 아카이브에 넣어요.");
    button.addEventListener("click", async () => {
      const article = extractArticle();
      if (!article) {
        note.className = "focus-error";
        note.textContent = "본문을 찾지 못했어요. 페이지가 다 열린 뒤 다시 눌러 주세요.";
        return;
      }
      button.disabled = true;
      button.textContent = "요약하는 중…";
      try {
        const saved = await api("/api/archive", { session_id: panel.sessionId, ...article, memo: memo.value });
        renderSaved(panel, saved, saved.cached);
        panel.refresh();
      } catch (err) {
        button.disabled = false;
        button.textContent = "이 페이지 담기";
        note.className = "focus-error";
        note.textContent = `담기 실패: ${err.message}`;
      }
    });
    panel.show("page", sectionTitle("이 페이지"), el("div", "focus-intent-text", pageTitle()), memo, button, note);
  }

  async function run() {
    const sessionId = await window.Focus.currentSession();
    if (!sessionId) return;
    const panel = window.Focus.createPanel({ sessionId, mode: "page", slots: ["page"] });
    panel.sessionId = sessionId;
    const session = await panel.refresh();
    if (!session) return;
    for (let i = 0; i < 5 && !extractArticle(); i++) await new Promise((r) => setTimeout(r, 600));
    const url = extractArticle()?.url || canonicalUrl(location);
    const saved = session.archive.find((a) => a.url === url);
    saved ? renderSaved(panel, saved, false) : renderPageCard(panel);
  }

  run();
})();
