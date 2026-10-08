async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try {
    data = await res.json();
  } catch {
  }
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join(", ")
      : data.detail;
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return data;
}


const $ = (id) => document.getElementById(id);
const naverUrl = (q) => `https://search.naver.com/search.naver?query=${encodeURIComponent(q)}`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function duration(sec) {
  if (sec < 60) return `${sec}초`;
  const m = Math.round(sec / 60);
  return m < 60 ? `${m}분` : `${Math.floor(m / 60)}시간 ${m % 60}분`;
}

function when(iso) {
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d) / 86400000);
  const hm = iso.slice(11, 16);
  if (days === 0) return `오늘 ${hm}`;
  if (days === 1) return `어제 ${hm}`;
  return `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${hm}`;
}

let toastTimer;
function toast(message) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2600);
}

function cite(n, sources) {
  const src = sources.find((s) => s.n === n);
  const a = el("a", "cite", String(n));
  if (src) {
    a.href = src.url;
    a.target = "_blank";
    a.title = src.title;
  }
  return a;
}

function citedText(text, sources) {
  const frag = document.createDocumentFragment();
  text.split(/(\s*\[\d+\])/).forEach((part) => {
    const m = part.match(/\[(\d+)\]/);
    const n = m && Number(m[1]);
    if (m && sources.some((s) => s.n === n)) frag.append(cite(n, sources));
    else frag.append(part);
  });
  return frag;
}

let extensionReady = false;
function setExtensionStatus() {
  $("ext-status").className = extensionReady ? "pill" : "pill pill-warn";
  $("ext-status").textContent = extensionReady ? "확장 연결됨" : "확장 미설치";
  $("install-banner").hidden = extensionReady;
}
window.addEventListener("message", (e) => {
  if (e.source === window && e.data?.source === "focus-ext" && e.data.type === "ready") {
    extensionReady = true;
    setExtensionStatus();
  }
});
window.postMessage({ source: "focus-page", type: "ping" }, "*");
setTimeout(setExtensionStatus, 600);

async function handoff(query, sessionId) {
  try {
    const session = sessionId ? { session_id: sessionId } : await api("/api/session", { query });
    if (!extensionReady) {
      window.open(naverUrl(query), "_blank");
      toast("확장 프로그램이 없어 필터 없이 네이버를 열었어요");
      go(`/?session=${session.session_id}`);
      return;
    }
    window.postMessage({ source: "focus-page", type: "open", session_id: session.session_id, query, sameTab: true }, "*");
  } catch (err) {
    toast(`시작하지 못했어요: ${err.message}`);
  }
}

$("start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = $("start-input").value.trim();
  if (!query) return;
  $("start-btn").disabled = true;
  await handoff(query);
  $("start-btn").disabled = false;
});
document.querySelectorAll(".chip-btn").forEach((chip) =>
  chip.addEventListener("click", () => {
    $("start-input").value = chip.textContent;
    $("start-input").focus();
  })
);

function stageOf(s) {
  if (s.has_report) return ["리포트 완성", "pill"];
  if (s.ended_at) return ["리포트 전", "pill pill-warn"];
  return ["검색 중", "pill"];
}

async function renderList() {
  const list = $("session-list");
  const sessions = await api("/api/sessions");
  $("session-count").textContent = sessions.length ? `${sessions.length}개` : "";
  const active = sessions.find((s) => !s.ended_at);
  $("resume").hidden = !active;
  if (active) {
    $("resume-query").textContent = active.query;
    $("resume-meta").textContent = `${when(active.started_at)} · 걸러냄 ${active.filtered} · 담은 글 ${active.archived}`;
    $("resume-dash").href = `/?session=${active.id}`;
    $("resume-go").onclick = () => handoff(active.query, active.id);
  }
  list.replaceChildren();
  if (!sessions.length) {
    list.append(el("div", "empty", "아직 학습 노트가 없어요. 위에서 첫 검색을 시작해 보세요."));
    return;
  }
  sessions.forEach((s) => {
    const card = el("a", "session-card");
    card.href = `/?session=${s.id}`;
    const [label, cls] = stageOf(s);
    const top = el("div", "row");
    top.append(el("span", cls, label), el("span", "muted small", when(s.started_at)));
    const stats = el("div", "session-stats");
    [["걸러냄", s.filtered], ["담은 글", s.archived], ["질문", s.asked]].forEach(([k, v]) => {
      const item = el("span", null, `${k} `);
      item.append(el("b", null, String(v)));
      stats.append(item);
    });
    card.append(top, el("strong", null, s.query), stats);
    list.append(card);
  });
}

let current = null;
let currentReport = null;
const TABS = ["overview", "posts", "ask", "report"];

function sourcesOf(session) {
  return session.archive.map((a, i) => ({ n: i + 1, title: a.title, url: a.url }));
}

function showTab(tab, push = true) {
  if (!TABS.includes(tab)) tab = "overview";
  TABS.forEach((t) => ($(`tab-${t}`).hidden = t !== tab));
  document.querySelectorAll("#stepper button").forEach((b) => b.classList.toggle("current", b.dataset.tab === tab));
  if (push && current) history.replaceState(null, "", `/?session=${current.id}&tab=${tab}`);
  if (tab === "ask") setTimeout(() => $("chat-input").focus(), 50);
}
document.querySelectorAll("#stepper button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

function renderStepper(s, r) {
  const done = {
    overview: r.results > 0,
    posts: s.archive.length > 0,
    ask: (s.qa || []).length > 0,
    report: !!s.learning_report,
  };
  $("st-overview").textContent = r.results ? `걸러냄 ${r.filtered}` : "아직 없음";
  $("st-posts").textContent = `${s.archive.length}개`;
  $("st-ask").textContent = (s.qa || []).length ? `질문 ${s.qa.length}` : "선택";
  $("st-report").textContent = s.learning_report ? "완성" : "";
  document.querySelectorAll("#stepper button").forEach((b) => b.classList.toggle("done", done[b.dataset.tab]));
}

function renderWrap(s, r) {
  const wrap = $("d-wrap");
  wrap.hidden = !s.ended_at;
  if (!s.ended_at) return;
  if (new URLSearchParams(location.search).get("done")) wrap.classList.add("celebrate");
  $("w-title").textContent = s.learning_report ? "학습 리포트가 준비됐어요" : "이제 모은 글로 리포트를 만들어 볼까요?";
  $("w-recap").textContent =
    `${duration(r.duration_sec)} 동안 결과 ${r.results}개 중 ${r.filtered}개를 걸러 약 ${duration(r.saved_sec_estimate)}을 아꼈고(추정), ` +
    `글 ${s.archive.length}개를 담았어요.`;
  $("w-report").textContent = s.learning_report ? "리포트 보기" : "학습 리포트 만들기";
  $("w-report").disabled = !s.archive.length;
  $("w-ask").disabled = !s.archive.length;
}

const LABEL_ROWS = [
  ["experience", "경험·정보", "var(--accent)"],
  ["promo", "홍보·광고", "var(--danger)"],
  ["offtrack", "샛길", "#8a8f98"],
  ["duplicate", "중복", "#a259c4"],
  ["unsure", "애매", "var(--warn)"],
];

function renderKpis(r) {
  const box = $("d-kpis");
  box.replaceChildren();
  const kpi = (value, label, accent) => {
    const k = el("div", accent ? "kpi kpi-accent" : "kpi");
    k.append(el("b", null, value), el("span", null, label));
    box.append(k);
  };
  kpi(`약 ${duration(r.saved_sec_estimate)}`, `아낀 시간 (추정 · 글당 ${r.seconds_per_filtered}초)`, true);
  kpi(`${r.filtered}개`, `걸러낸 글 / 판별 ${r.results}개`);
  kpi(`${r.archived}개`, "담은 글");
  kpi(r.tokens.toLocaleString(), "사용 토큰");
}

function renderJudge(r) {
  const box = $("d-report");
  const counts = { ...r.labels, duplicate: r.duplicates };
  const max = Math.max(1, ...Object.values(counts));
  const bars = el("div", "bars");
  LABEL_ROWS.forEach(([key, label, color]) => {
    const row = el("div", "bar-row");
    const bar = el("div", "bar");
    const fill = el("span");
    fill.style.width = `${(counts[key] / max) * 100}%`;
    fill.style.background = color;
    bar.append(fill);
    row.append(el("span", null, label), bar, el("b", null, String(counts[key])));
    bars.append(row);
  });
  const st = r.stages;
  box.replaceChildren(
    bars,
    el("p", "muted small stage-line", `판단이 끝난 단계 — 규칙 ${st.rule} · 임베딩 ${st.embed} · DASH-002 ${st.fast} · HCX-007 ${st.deep}`)
  );
  if (r.comparison) {
    const m = r.comparison.measured;
    const table = el("table", "compare");
    const head = el("tr");
    [`같은 결과 ${m.items}개 실측`, "첫 판정", "HCX-007 토큰", "정답률"].forEach((h) => head.append(el("th", null, h)));
    table.append(head);
    [["우리 파이프라인", m.ours, "ours"], ["전부 HCX-007이었다면", m.all_deep, ""]].forEach(([label, s, cls]) => {
      const tr = el("tr", cls);
      [label, `${s.first_seconds}초`, s.deep_tokens.toLocaleString(), s.accuracy == null ? "-" : `${Math.round(s.accuracy * 100)}%`].forEach((v) =>
        tr.append(el("td", null, v))
      );
      table.append(tr);
    });
    box.append(table);
  }
}

function renderRoadmap(steps) {
  const list = $("d-roadmap");
  const done = steps.filter((s) => s.done).length;
  $("d-roadmap-progress").textContent = steps.length ? `${done} / ${steps.length}` : "";
  $("d-roadmap-bar").style.width = steps.length ? `${(done / steps.length) * 100}%` : "0";
  list.replaceChildren();
  if (!steps.length) {
    list.append(el("li", "empty-card", "네이버에서 검색하면 다음에 찾아볼 순서를 만들어 드려요."));
    return;
  }
  steps.forEach((step, i) => {
    const li = el("li", step.done ? "done" : "");
    const check = el("input");
    check.type = "checkbox";
    check.checked = step.done;
    check.addEventListener("change", async () => {
      const res = await api(`/api/session/${current.id}/roadmap`, { index: i, done: check.checked });
      current.roadmap = res.steps;
      renderRoadmap(res.steps);
    });
    const body = el("div");
    body.append(el("div", "step-title", step.title), el("div", "muted small", step.why));
    const link = el("a", null, `'${step.search_query}' 검색하기 →`);
    link.href = naverUrl(step.search_query);
    link.addEventListener("click", (e) => {
      e.preventDefault();
      current.ended_at ? handoff(step.search_query) : handoff(step.search_query, current.id);
    });
    body.append(link);
    li.append(check, body);
    list.append(li);
  });
}

function renderArchive(archive) {
  const box = $("d-archive");
  $("d-archive-count").textContent = archive.length;
  box.replaceChildren();
  if (!archive.length) {
    box.append(el("div", "empty-card", "검색하다 좋은 글을 열고 패널의 '이 페이지 담기'를 누르면 여기에 모여요."));
    return;
  }
  archive.forEach((post, i) => {
    const card = el("div", "post");
    const title = el("a", "title");
    title.href = post.url;
    title.target = "_blank";
    title.append(el("span", "post-no", `[${i + 1}]`), post.title);
    const ul = el("ul");
    post.summary.forEach((line) => ul.append(el("li", null, line)));
    const chips = el("div", "chips");
    post.keywords.forEach((k) => chips.append(el("span", "chip", `#${k}`)));
    const memo = el("textarea");
    memo.placeholder = "메모 남기기 (자동 저장) — 리포트에서 강조할 점을 적어 두세요";
    memo.value = post.memo || "";
    memo.addEventListener("change", async () => {
      await api("/api/archive", { session_id: current.id, url: post.url, memo: memo.value }, "PATCH");
      post.memo = memo.value;
      toast("메모를 저장했어요");
    });
    const foot = el("div", "post-foot");
    const remove = el("button", "btn btn-sm btn-ghost-danger", "빼기");
    remove.addEventListener("click", async () => {
      await api(`/api/archive?session_id=${current.id}&url=${encodeURIComponent(post.url)}`, undefined, "DELETE");
      toast("아카이브에서 뺐어요");
      loadSession(current.id);
    });
    foot.append(el("span", "muted small", `${when(post.added_at)} 담음`), remove);
    card.append(title, ul, chips, memo, foot);
    box.append(card);
  });
}

const SUGGESTIONS = ["처음 시작하려면 뭐부터 해야 해?", "글들이 공통으로 강조하는 건?", "가장 많이 하는 실수는?", "글마다 다르게 말하는 부분은?"];

function bubble(role, children) {
  const b = el("div", `msg msg-${role}`);
  b.append(...children);
  $("chat-log").append(b);
  b.scrollIntoView({ block: "end", behavior: "smooth" });
  return b;
}

function notFoundActions(question) {
  const row = el("div", "row msg-actions");
  const search = el("button", "btn btn-sm btn-primary", `'${question.slice(0, 20)}' 검색해서 더 모으기`);
  search.addEventListener("click", () => (current.ended_at ? handoff(question) : handoff(question, current.id)));
  row.append(search);
  return row;
}

function renderAnswer(b, text, sources, used) {
  const body = el("div", "msg-text");
  body.append(citedText(text, sources));
  const children = [body];
  if (used?.length) {
    const refs = el("div", "msg-refs");
    refs.append(el("span", "muted small", "근거: "));
    used.forEach((n) => {
      const s = sources.find((x) => x.n === n);
      if (!s) return;
      const a = el("a", null, `[${n}] ${s.title}`);
      a.href = s.url;
      a.target = "_blank";
      refs.append(a);
    });
    children.push(refs);
  }
  b.replaceChildren(...children);
}

function renderChat(s) {
  const log = $("chat-log");
  log.replaceChildren();
  const sources = sourcesOf(s);
  if (!(s.qa || []).length) {
    log.append(
      el(
        "div",
        "chat-empty",
        s.archive.length ? `담은 글 ${s.archive.length}개에게 무엇이든 물어보세요. 물어본 내용은 리포트에서 무엇을 강조할지 정하는 데 쓰여요.` : "먼저 글을 담아야 물어볼 수 있어요."
      )
    );
  }
  (s.qa || []).forEach((qa) => {
    bubble("user", [el("div", "msg-text", qa.question)]);
    const b = bubble("ai", []);
    renderAnswer(b, qa.answer, sources, qa.sources);
    if (!qa.found) b.append(notFoundActions(qa.question));
  });
  const suggest = $("chat-suggest");
  suggest.replaceChildren();
  SUGGESTIONS.forEach((q) => {
    const c = el("button", "chip-btn", q);
    c.type = "button";
    c.addEventListener("click", () => ask(q));
    suggest.append(c);
  });
  $("chat-input").disabled = $("chat-btn").disabled = !s.archive.length;
}

async function ask(question) {
  if (!question || $("chat-btn").disabled) return;
  $("chat-input").value = "";
  $("chat-btn").disabled = true;
  $(`chat-log`).querySelector(".chat-empty")?.remove();
  bubble("user", [el("div", "msg-text", question)]);
  const b = bubble("ai", [el("div", "msg-text typing", "담은 글에서 관련 부분을 찾는 중…")]);
  const sources = sourcesOf(current);
  try {
    const res = await fetch(`/api/session/${current.id}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error(`${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let meta = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      if (!meta) {
        const nl = buf.indexOf("\n");
        if (nl < 0) continue;
        meta = JSON.parse(buf.slice(0, nl));
        buf = buf.slice(nl + 1);
      }
      renderAnswer(b, buf.split("\u001e")[0].trimEnd(), sources, null);
      b.scrollIntoView({ block: "end" });
    }
    const [text, tail] = buf.split("\u001e");
    const final = tail ? JSON.parse(tail) : { found: meta?.found };
    const used = final.found ? (final.cited?.length ? final.cited : (meta?.sources || []).map((s) => s.n)) : [];
    renderAnswer(b, text.trimEnd(), sources, used);
    if (!final.found) b.append(notFoundActions(question));
    current.qa = [...(current.qa || []), { question, answer: text, sources: used, found: final.found }];
    renderStepper(current, currentReport);
  } catch (err) {
    b.replaceChildren(el("div", "msg-text error", `답하지 못했어요: ${err.message}`));
  } finally {
    $("chat-btn").disabled = false;
    $("chat-input").focus();
  }
}
$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  ask($("chat-input").value.trim());
});

function section(title, ...children) {
  const sec = el("section", "r-section");
  sec.append(el("h2", null, title), ...children);
  return sec;
}

function citeList(nums, sources) {
  const span = el("span", "cites");
  nums.forEach((n) => span.append(cite(n, sources)));
  return span;
}

function claimList(items, sources, tag = "ul", label) {
  const list = el(tag, "r-list");
  items.forEach((it) => {
    const li = el("li");
    if (label && it[label]) li.append(el("b", null, it[label]), " ");
    li.append(it.text, citeList(it.sources, sources));
    list.append(li);
  });
  return list;
}

function renderQuiz(quiz, sources) {
  const wrap = el("div", "quiz");
  quiz.forEach((q, qi) => {
    const box = el("div", "quiz-q");
    box.append(el("p", "quiz-title", `Q${qi + 1}. ${q.question}`));
    const choices = el("div", "quiz-choices");
    const explain = el("p", "quiz-explain");
    explain.hidden = true;
    q.choices.forEach((c, ci) => {
      const btn = el("button", "quiz-choice", c);
      btn.addEventListener("click", () => {
        choices.querySelectorAll("button").forEach((x, xi) => {
          x.disabled = true;
          if (xi === q.answer) x.classList.add("right");
        });
        if (ci !== q.answer) btn.classList.add("wrong");
        explain.hidden = false;
        explain.replaceChildren(ci === q.answer ? "정답이에요! " : "아쉬워요. ", q.explain, citeList([q.source], sources));
      });
      choices.append(btn);
    });
    box.append(choices, explain);
    wrap.append(box);
  });
  return wrap;
}

function renderReportDoc(rep, s, r) {
  const doc = $("report-doc");
  const sources = rep.sources;
  doc.replaceChildren();

  const head = el("header", "r-head");
  const actions = el("div", "row r-actions no-print");
  const again = el("button", "btn btn-sm", "다시 만들기");
  again.id = "r-again";
  again.addEventListener("click", generateReport);
  const md = el("button", "btn btn-sm", "마크다운");
  md.addEventListener("click", exportMarkdown);
  const print = el("button", "btn btn-sm btn-primary", "PDF로 저장");
  print.addEventListener("click", () => window.print());
  actions.append(again, md, print);
  head.append(
    el("p", "eyebrow", `학습 리포트 · ${when(rep.created_at || new Date().toISOString())}`),
    el("h1", null, rep.title),
    el("p", "tldr", rep.tldr),
    el("p", "muted small", `'${s.query}' 검색에서 직접 담은 글 ${sources.length}개만 근거로 HCX-007이 썼어요. 숫자 배지를 누르면 원문으로 가요.`),
    actions
  );
  doc.append(head);

  if (rep.key_points.length) doc.append(section("핵심 정리", claimList(rep.key_points, sources, "ol")));
  if (rep.common.length || rep.differences.length) {
    const two = el("div", "r-two");
    const a = el("div", "r-col");
    a.append(el("h3", null, "여러 글이 함께 말해요"), rep.common.length ? claimList(rep.common, sources) : el("p", "muted small", "두 글 이상이 함께 말한 점이 없었어요."));
    const b = el("div", "r-col");
    b.append(el("h3", null, "글마다 달라요"), rep.differences.length ? claimList(rep.differences, sources) : el("p", "muted small", "엇갈리는 점이 없었어요."));
    two.append(a, b);
    doc.append(section("비교", two));
  }
  if (rep.action_plan.length) {
    const ol = el("ol", "timeline");
    rep.action_plan.forEach((it, i) => {
      const li = el("li");
      li.append(el("span", "t-no", String(i + 1)));
      const body = el("div");
      body.append(el("b", null, it.step || `${i + 1}단계`), el("p", null, it.text), citeList(it.sources, sources));
      li.append(body);
      ol.append(li);
    });
    doc.append(section("이렇게 해 보세요", ol));
  }
  if (rep.cautions.length) {
    const ul = el("ul", "cautions");
    rep.cautions.forEach((it) => {
      const li = el("li");
      li.append(it.text, citeList(it.sources, sources));
      ul.append(li);
    });
    doc.append(section("주의할 점", ul));
  }
  if (rep.glossary.length) {
    const dl = el("dl", "glossary");
    rep.glossary.forEach((it) => {
      const dd = el("dd");
      dd.append(it.text, citeList(it.sources, sources));
      dl.append(el("dt", null, it.term), dd);
    });
    doc.append(section("용어", dl));
  }
  if (rep.quiz?.length) doc.append(section("이해 확인", renderQuiz(rep.quiz, sources)));
  if (rep.open_questions.length) {
    const box = el("div", "next-queries");
    rep.open_questions.forEach((q) => {
      const b = el("button", "next-query");
      b.append(el("b", null, q), el("span", null, "이 주제로 새 집중 검색 →"));
      b.addEventListener("click", () => handoff(q));
      box.append(b);
    });
    doc.append(section("더 알아볼 것", el("p", "muted small", "담은 글로는 답이 안 된 부분이에요."), box));
  }

  const ol = el("ol", "r-sources");
  s.archive.slice(0, sources.length).forEach((a, i) => {
    const li = el("li");
    const link = el("a", null, a.title);
    link.href = a.url;
    link.target = "_blank";
    li.append(link, el("div", "muted small", a.url));
    if (a.memo) li.append(el("div", "memo", `내 메모: ${a.memo}`));
    ol.append(li);
  });
  doc.append(section("출처", ol));

  const usage = Object.entries(r.usage)
    .map(([m, u]) => `${m} ${u.calls}회`)
    .join(" · ");
  const foot = el("footer", "r-foot");
  foot.append(
    el("b", null, "이 리포트가 만들어지기까지"),
    el(
      "p",
      null,
      `검색 ${s.queries.length || 1}번 · 결과 ${r.results}개 판별 · ${r.filtered}개 걸러냄 · 글 ${s.archive.length}개 담음 · 질문 ${(s.qa || []).length}개 · ` +
        `토큰 ${r.tokens.toLocaleString()}`
    ),
    el("p", "muted small", usage)
  );
  if (rep.dropped) foot.append(el("p", "muted small", `출처가 확인되지 않은 문장 ${rep.dropped}개는 리포트에서 뺐어요.`));
  doc.append(foot);
}

function renderReportTab(s, r) {
  const rep = s.learning_report;
  $("report-empty").hidden = !!rep;
  $("report-doc").hidden = !rep;
  $("r-generate").disabled = !s.archive.length;
  $("r-note").textContent = s.archive.length
    ? `담은 글 ${s.archive.length}개${(s.qa || []).length ? `와 질문 ${s.qa.length}개` : ""}를 바탕으로 만들어요.${s.archive.length < 2 ? " 글이 2개 이상이면 비교도 해 드려요." : ""}`
    : "먼저 검색하며 글을 담아 주세요.";
  if (rep) renderReportDoc(rep, s, r);
}

async function generateReport() {
  const buttons = ["r-generate", "w-report", "r-again"].map((id) => $(id)).filter(Boolean);
  const labels = buttons.map((b) => b.textContent);
  buttons.forEach((b) => {
    b.disabled = true;
    b.textContent = "리포트 만드는 중…";
  });
  try {
    await api(`/api/session/${current.id}/learning-report`, {});
    await loadSession(current.id);
    showTab("report");
  } catch (err) {
    buttons.forEach((b, i) => {
      b.disabled = false;
      b.textContent = labels[i];
    });
    showTab("report");
    $("r-note").textContent = `리포트를 만들지 못했어요: ${err.message}`;
    toast(`리포트를 만들지 못했어요: ${err.message}`);
  }
}
$("r-generate").addEventListener("click", generateReport);

async function exportMarkdown() {
  const text = await (await fetch(`/api/session/${current.id}/export.md`)).text();
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  a.download = `학습리포트-${current.query.slice(0, 20)}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("마크다운 파일을 저장했어요");
}

async function loadSession(id) {
  const [session, report] = await Promise.all([api(`/api/session/${id}`), api(`/api/session/${id}/report`)]);
  current = session;
  currentReport = report;
  document.title = `${session.query} · CutLing`;
  $("d-query").textContent = session.query;
  $("d-status").className = session.ended_at ? "pill pill-muted" : "pill";
  $("d-status").textContent = session.ended_at ? "검색 완료" : "검색 중";
  $("d-meta").textContent = `${when(session.started_at)} 시작` + (session.queries.length > 1 ? ` · 검색어 ${session.queries.length}개` : "");
  $("d-intent").textContent = session.intent ? `검색 의도: ${session.intent}` : "";
  $("d-end").hidden = !!session.ended_at;
  $("d-continue").textContent = session.ended_at ? "이 주제로 다시 검색" : "이어서 검색";
  renderStepper(session, report);
  renderWrap(session, report);
  renderKpis(report);
  renderJudge(report);
  renderRoadmap(session.roadmap);
  renderArchive(session.archive);
  renderChat(session);
  renderReportTab(session, report);
}

$("d-continue").addEventListener("click", () =>
  current.ended_at ? handoff(current.query) : handoff(current.queries.at(-1) || current.query, current.id)
);
$("d-end").addEventListener("click", async () => {
  await api(`/api/session/${current.id}/end`, {});
  window.postMessage({ source: "focus-page", type: "end", session_id: current.id }, "*");
  go(`/?session=${current.id}&done=1`);
});
$("w-report").addEventListener("click", () => (current.learning_report ? showTab("report") : generateReport()));
$("w-ask").addEventListener("click", () => showTab("ask"));

const del = el("button", "btn btn-sm btn-ghost-danger delete-btn", "이 학습 노트 삭제");
del.addEventListener("click", async () => {
  if (!confirm("이 세션의 검색 기록·담은 글·리포트를 모두 지울까요? 되돌릴 수 없어요.")) return;
  await api(`/api/session/${current.id}`, undefined, "DELETE");
  window.postMessage({ source: "focus-page", type: "end", session_id: current.id }, "*");
  toast("삭제했어요");
  go("/?view=notes");
});
$("tab-overview").append(del);

function go(url) {
  history.pushState(null, "", url);
  route();
}

async function route() {
  const params = new URLSearchParams(location.search);
  const id = params.get("session");
  const notes = !id && params.get("view") === "notes";
  document.body.dataset.view = id ? "detail" : notes ? "notes" : "home";
  $("home-view").hidden = !!id || notes;
  $("notes-view").hidden = !notes;
  $("detail-view").hidden = !id;
  window.scrollTo(0, 0);
  try {
    if (!id) {
      document.title = notes ? "내 학습 노트 · CutLing" : "CutLing";
      current = null;
      if (notes) await renderList();
      return;
    }
    await loadSession(id);
    const tab = params.get("tab") || (current.ended_at ? (current.learning_report ? "report" : "posts") : "overview");
    showTab(tab, false);
  } catch (err) {
    toast(`불러오지 못했어요: ${err.message}`);
  }
}
window.addEventListener("popstate", route);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && current && !$("detail-view").hidden && !$("chat-btn").disabled) loadSession(current.id).catch(() => {});
});
route();
