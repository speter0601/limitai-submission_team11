(() => {
  const VERDICT_LABEL = { experience: "경험·정보", promo: "홍보", offtrack: "샛길", unsure: "판단 중" };
  const STAGE_LABEL = { rule: "규칙", embed: "임베딩", fast: "DASH", deep: "007" };
  const COLLAPSED = ["promo", "offtrack"];

  const query = new URLSearchParams(location.search).get("query");
  if (!query) return;

  const { api, el, sectionTitle } = window.Focus;

  const state = { sessionId: null, intent: "", items: [], verdicts: {}, adCount: 0 };

  const AD_SECTION = ".ad_section, ._pl_section, [id^='pcPowerLink'], [data-focus-ad]";
  function isAdArea(card, link) {
    return /(ader|adcr)\.naver\.com/.test(link.href) || !!card.closest(AD_SECTION) || !!card.querySelector(".ad_mark");
  }

  function adMarks(root = document) {
    const marks = [...root.querySelectorAll(".ad_mark, [aria-label='광고']")];
    root.querySelectorAll("span, em, i, strong").forEach((n) => {
      if (!n.childElementCount && n.textContent.trim() === "광고") marks.push(n);
    });
    return marks.filter((m) => !m.closest(".focus-panel, .focus-badge"));
  }

  function markAdBlocks() {
    adMarks().forEach((mark) => {
      const block = mark.closest(AD_SECTION) || mark.closest("section") || mark.closest(".sc_new");
      if (block && block !== document.body && !block.closest(".focus-panel")) block.dataset.focusAd = "1";
    });
  }

  function collectAds() {
    markAdBlocks();
    const ads = [];
    document.querySelectorAll(AD_SECTION).forEach((section, n) => {
      if (section.parentElement?.closest(AD_SECTION)) return;
      let entries = [...section.querySelectorAll("li.lst")];
      if (!entries.length) {
        entries = [
          ...new Set(
            adMarks(section).map((mark) => {
              let node = mark;
              while (node.parentElement && node.parentElement !== section && adMarks(node.parentElement).length === 1) {
                node = node.parentElement;
              }
              return node;
            })
          ),
        ];
      }
      if (!entries.length) return;
      entries.forEach((entry, i) => {
        const title =
          [...entry.querySelectorAll(".lnk_tit")].map((t) => t.textContent.trim()).join(" ") ||
          (entry.querySelector("a")?.textContent || entry.textContent).trim().slice(0, 80);
        const host = entry.querySelector(".lnk_url")?.textContent.trim() || "";
        ads.push({
          id: `focus-ad-${n}-${i}`,
          title: title || "광고",
          snippet: entry.querySelector(".link_desc")?.textContent.trim() || "",
          url: `ad:${host || title}`,
          is_ad: true,
        });
      });
      section.classList.add("focus-card", "focus-promo", "focus-collapsed", "focus-ad-block");
      section.querySelector(":scope > .focus-badge")?.remove();
      const badge = el("div", "focus-badge");
      badge.append(
        el("span", "focus-tag focus-tag-promo", `광고 ${entries.length}개`),
        el("span", "focus-reason", "광고 영역이라 접어 뒀어요"),
        el("span", "focus-stage", "규칙")
      );
      const toggle = el("button", "focus-btn", "펼치기");
      toggle.addEventListener("click", () => {
        toggle.textContent = section.classList.toggle("focus-open") ? "접기" : "펼치기";
      });
      badge.append(toggle);
      section.prepend(badge);
    });
    return ads;
  }

  function collectItems() {
    const titles = document.querySelectorAll('a[data-heatmap-target=".link"] .sds-comps-text-type-headline1');
    const seen = new Set();
    const items = [];
    titles.forEach((titleNode) => {
      const link = titleNode.closest("a");
      if (!link || seen.has(link.href) || link.closest(AD_SECTION)) return;
      seen.add(link.href);
      let card = link;
      while (
        card.parentElement &&
        card.parentElement !== document.body &&
        card.parentElement.querySelectorAll(".sds-comps-text-type-headline1").length === 1
      ) {
        card = card.parentElement;
      }
      const snippetNode =
        card.querySelector(".sds-comps-text-content") || card.querySelector(".sds-comps-text-ellipsis-3");
      const id = `focus-${items.length}`;
      card.dataset.focusId = id;
      card.classList.add("focus-card");
      items.push({
        id,
        title: titleNode.textContent.trim(),
        snippet: snippetNode ? snippetNode.textContent.trim() : "",
        url: link.href,
        is_ad: isAdArea(card, link),
        card,
      });
    });
    return items;
  }

  function applyVerdict(result) {
    const item = state.items.find((it) => it.id === result.id);
    if (!item) return;
    const prev = state.verdicts[result.id] || {};
    result = { ...prev, ...result };
    state.verdicts[result.id] = result;
    const { card } = item;
    const collapsed = COLLAPSED.includes(result.verdict) || !!result.duplicate_of;
    card.classList.remove("focus-experience", "focus-promo", "focus-offtrack", "focus-unsure", "focus-collapsed", "focus-open");
    card.classList.add(`focus-${result.verdict}`);
    if (collapsed) card.classList.add("focus-collapsed");
    card.querySelector(":scope > .focus-badge")?.remove();

    const badge = el("div", "focus-badge");
    if (result.duplicate_of) badge.append(el("span", "focus-tag focus-tag-dup", "중복"));
    else badge.append(el("span", `focus-tag focus-tag-${result.verdict}`, VERDICT_LABEL[result.verdict]));
    if (result.seen) badge.append(el("span", "focus-tag focus-tag-seen", "이미 본 글"));
    badge.append(el("span", "focus-reason", result.reason || ""));
    badge.append(el("span", "focus-stage", STAGE_LABEL[result.stage] || ""));
    if (collapsed) {
      const toggle = el("button", "focus-btn", "펼치기");
      toggle.addEventListener("click", () => {
        toggle.textContent = card.classList.toggle("focus-open") ? "접기" : "펼치기";
      });
      badge.append(toggle);
    }
    card.prepend(badge);
  }

  let panel = null;

  function renderIntent(data) {
    const children = [sectionTitle("검색 의도"), el("div", "focus-intent-text", data.intent)];
    if (data.chips?.length) {
      const chips = el("div", "focus-chips");
      data.chips.forEach((c) => chips.append(el("span", "focus-chip", c)));
      children.push(chips);
    }
    panel.show("intent", ...children);
  }

  async function loadRoadmap(results) {
    const session = panel.session || (await panel.refresh());
    if (!session || session.roadmap.length) return;
    const good = results.filter((r) => r.verdict === "experience" && !r.duplicate_of).map((r) => r.id);
    const items = state.items.filter((it) => good.includes(it.id)).map(({ title, snippet }) => ({ title, snippet }));
    panel.show("roadmap", sectionTitle("다음 검색 로드맵"), el("div", "focus-muted", "검색 결과를 보고 다음 순서를 짜는 중…"));
    try {
      const res = await api("/api/focus/roadmap", { session_id: state.sessionId, query, intent: state.intent, items });
      panel.renderRoadmap(res.steps);
    } catch (err) {
      panel.show("roadmap", sectionTitle("다음 검색 로드맵"), el("div", "focus-error", `로드맵 실패: ${err.message}`));
    }
  }

  async function run() {
    state.sessionId = await window.Focus.currentSession();
    if (!state.sessionId) return;

    panel = window.Focus.createPanel({ sessionId: state.sessionId, mode: "search", slots: ["intent"] });
    const ads = collectAds();
    state.adCount = ads.length;
    state.items = collectItems();
    await panel.refresh();
    if (!state.items.length && !ads.length) {
      panel.setStatus("읽을 수 있는 검색 결과가 없어요. 네이버 화면 구조가 바뀌었을 수 있어요.");
      return;
    }
    panel.setStatus(`'${query}' 결과 ${state.items.length}개를 판별하는 중…` + (ads.length ? ` · 광고 ${ads.length}개는 접었어요` : ""), true);
    const payload = state.items.map(({ id, title, snippet, url, is_ad }) => ({ id, title, snippet, url, is_ad }));
    try {
      const data = await api("/api/focus/filter", { session_id: state.sessionId, query, items: [...payload, ...ads].slice(0, 30) });
      state.intent = data.intent;
      renderIntent(data);
      data.results.forEach(applyVerdict);
      panel.setStatus(`판별 완료 · ${(data.elapsed_ms / 1000).toFixed(1)}초${data.cached ? " (캐시)" : ""}`);
      panel.refresh().then(() => loadRoadmap(data.results));

      const unsure = data.results.filter((r) => r.verdict === "unsure" && !r.duplicate_of).map((r) => r.id);
      if (!unsure.length) return;
      panel.setStatus(`애매한 ${unsure.length}개는 HCX-007이 다시 보는 중…`, true);
      const deep = await api("/api/focus/deep", {
        session_id: state.sessionId,
        intent: state.intent,
        items: payload.filter((it) => unsure.includes(it.id)),
      });
      deep.results.forEach(applyVerdict);
      panel.setStatus(`판별 완료 · HCX-007 ${(deep.elapsed_ms / 1000).toFixed(1)}초`);
      panel.refresh();
    } catch (err) {
      panel.setError(`서버 연결 실패: ${err.message} (localhost:8000 서버가 켜져 있는지 확인하세요)`);
    }
  }

  run();
})();
