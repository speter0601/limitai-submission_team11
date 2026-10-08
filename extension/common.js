(() => {
  if (window.Focus) return;

  const DASHBOARD = "http://localhost:8000/?session=";
  const naverUrl = (q) => `https://search.naver.com/search.naver?query=${encodeURIComponent(q)}`;

  function send(msg) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(msg, (res) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        res?.ok ? resolve(res.data) : reject(new Error(res?.error || "응답 없음"));
      });
    });
  }
  const api = (path, body, method) => send({ type: "focus-api", path, body, method });

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function sectionTitle(text, extra) {
    const h = el("div", "focus-section-title");
    h.append(el("span", null, text));
    if (extra != null) h.append(el("span", "focus-muted", extra));
    return h;
  }

  function createPanel({ sessionId, mode, slots }) {
    const root = el("aside", `focus-panel focus-panel-${mode}`);
    const header = el("div", "focus-panel-header");
    const brand = el("div", "focus-brand");
    const mark = el("img", "focus-brand-mark");
    mark.src = chrome.runtime.getURL("brand/symbol-white.png");
    mark.alt = "";
    brand.append(mark, el("strong", null, "CutLing"));
    const minimize = el("button", "focus-icon-btn", "—");
    minimize.title = "패널 접기";
    header.append(brand, minimize);
    const queryLine = el("div", "focus-query", "세션 불러오는 중…");

    const body = el("div", "focus-panel-body");
    const status = el("div", "focus-status");
    status.hidden = true;
    body.append(status);
    const slotEls = {};
    for (const name of ["kpis", ...slots, "roadmap", "archive"]) {
      slotEls[name] = el(name === "kpis" ? "div" : "section", name === "kpis" ? "focus-kpis" : "focus-section");
      slotEls[name].hidden = name !== "kpis";
      body.append(slotEls[name]);
    }

    const actions = el("div", "focus-actions");
    const dashboardBtn = el("button", "focus-btn focus-secondary", "대시보드");
    const endBtn = el("button", "focus-btn focus-primary", "검색 마치고 리포트 받기");
    actions.append(dashboardBtn, endBtn);
    root.append(header, queryLine, body, actions);

    const setMin = (min) => {
      root.classList.toggle("focus-panel-min", min);
      minimize.textContent = min ? "+" : "—";
      minimize.title = min ? "패널 펼치기" : "패널 접기";
    };
    chrome.storage.local.get("focusPanelMin", (v) => setMin(!!v.focusPanelMin));
    minimize.addEventListener("click", () => {
      const min = !root.classList.contains("focus-panel-min");
      setMin(min);
      chrome.storage.local.set({ focusPanelMin: min });
    });
    header.addEventListener("dblclick", () => minimize.click());

    dashboardBtn.addEventListener("click", () => send({ type: "open-dashboard", session_id: sessionId }));
    endBtn.addEventListener("click", async () => {
      endBtn.disabled = true;
      endBtn.textContent = "리포트 화면으로 가는 중…";
      try {
        await api(`/api/session/${sessionId}/end`, {});
      } catch {
      }
      await send({ type: "end-session", session_id: sessionId });
      location.href = `${DASHBOARD}${sessionId}&done=1`;
    });

    const panel = {
      root,
      slot: (name) => slotEls[name],
      show(name, ...children) {
        slotEls[name].hidden = false;
        slotEls[name].replaceChildren(...children);
      },
      setStatus(text, busy) {
        status.hidden = !text;
        status.className = busy ? "focus-status focus-busy" : "focus-status";
        status.textContent = text || "";
      },
      setError(text) {
        status.hidden = false;
        status.className = "focus-status focus-error";
        status.textContent = text;
      },
      session: null,

      renderKpis(r) {
        const kpis = slotEls.kpis;
        kpis.replaceChildren();
        [
          [`${r.filtered}`, "걸러낸 글", true],
          [`${Math.round(r.saved_sec_estimate / 60)}분`, "아낀 시간·추정"],
          [`${r.archived}`, "담은 글"],
        ].forEach(([v, label, accent]) => {
          const k = el("div", accent ? "focus-kpi focus-kpi-accent" : "focus-kpi");
          k.append(el("b", null, v), el("span", null, label));
          kpis.append(k);
        });
        kpis.title = `세션 전체 · 토큰 ${r.tokens.toLocaleString()} · 걸러낸 글당 ${r.seconds_per_filtered}초로 추정`;
      },

      renderRoadmap(steps) {
        if (!steps.length) return;
        const done = steps.filter((s) => s.done).length;
        const progress = el("div", "focus-progress");
        const fill = el("span");
        fill.style.width = `${(done / steps.length) * 100}%`;
        progress.append(fill);
        const list = el("ol", "focus-steps");
        steps.forEach((step, i) => {
          const li = el("li", step.done ? "focus-done" : "");
          const check = el("input");
          check.type = "checkbox";
          check.checked = step.done;
          check.addEventListener("change", async () => {
            const res = await api(`/api/session/${sessionId}/roadmap`, { index: i, done: check.checked });
            panel.renderRoadmap(res.steps);
          });
          const text = el("div", "focus-step-text");
          const link = el("a", "focus-step-link", step.title);
          link.href = naverUrl(step.search_query);
          text.append(link, el("div", "focus-step-query", step.search_query));
          text.title = step.why;
          li.append(check, text);
          list.append(li);
        });
        panel.show("roadmap", sectionTitle("다음 검색 로드맵", `${done}/${steps.length}`), progress, list);
      },

      renderArchive(archive) {
        if (!archive.length) {
          panel.show("archive", sectionTitle("담은 글", "0"), el("div", "focus-muted", "읽다가 좋은 글이면 '이 페이지 담기'를 눌러 모아 두세요."));
          return;
        }
        const list = el("ol", "focus-archive-list");
        archive
          .slice()
          .reverse()
          .slice(0, 5)
          .forEach((a) => {
            const li = el("li");
            const link = el("a", null, a.title);
            link.href = a.url;
            link.target = "_blank";
            li.append(link);
            if (a.keywords?.length) li.append(el("div", "focus-step-query", a.keywords.map((k) => `#${k}`).join(" ")));
            list.append(li);
          });
        const children = [sectionTitle("담은 글", `${archive.length}`), list];
        if (archive.length >= 1) {
          const hint = el("div", "focus-hint", "검색을 마치면 담은 글만으로 학습 리포트를 만들어 드려요. 그 전에 담은 글에게 질문할 수도 있어요.");
          children.push(hint);
        }
        panel.show("archive", ...children);
      },

      async refresh() {
        try {
          const [session, report] = await Promise.all([
            api(`/api/session/${sessionId}`),
            api(`/api/session/${sessionId}/report`),
          ]);
          panel.session = session;
          queryLine.textContent = session.query;
          queryLine.title = session.queries.length > 1 ? `이 세션의 검색어: ${session.queries.join(", ")}` : "";
          panel.renderKpis(report);
          panel.renderRoadmap(session.roadmap);
          panel.renderArchive(session.archive);
          return session;
        } catch (err) {
          panel.setError(`서버 연결 실패: ${err.message} (localhost:8000 서버가 켜져 있는지 확인하세요)`);
          return null;
        }
      },
    };

    document.body.append(root);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) panel.refresh();
    });
    return panel;
  }

  async function currentSession() {
    return send({ type: "get-session" }).catch(() => null);
  }

  window.Focus = { send, api, el, sectionTitle, createPanel, currentSession, naverUrl };
})();
