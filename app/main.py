from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import focus, learn, sessions

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(http://(localhost|127\.0\.0\.1)(:\d+)?|chrome-extension://[a-p]{32})$",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


def _session(session_id: str) -> dict:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "세션을 찾을 수 없어요.")
    return session


class SessionRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100)


class StepRequest(BaseModel):
    index: int
    done: bool


@app.post("/api/session")
def session_start(req: SessionRequest):
    s = sessions.create(req.query.strip())
    return {"session_id": s["id"], "query": s["query"], "started_at": s["started_at"]}


@app.get("/api/sessions")
def session_list():
    return [
        {k: s[k] for k in ("id", "query", "started_at", "ended_at")}
        | {
            "archived": len(s["archive"]),
            "results": len(s["results"]),
            "filtered": sum(r["duplicate"] or r["verdict"] in sessions.FILTERED for r in s["results"].values()),
            "roadmap_done": sum(st["done"] for st in s["roadmap"]),
            "roadmap_total": len(s["roadmap"]),
            "asked": len(s.get("qa", [])),
            "has_report": bool(s.get("learning_report")),
        }
        for s in sessions.all_sessions()
    ]


@app.get("/api/session/{session_id}")
def session_detail(session_id: str):
    s = _session(session_id)
    return {k: v for k, v in s.items() if k != "results"} | {
        "archive": [{k: v for k, v in a.items() if k != "text"} for a in s["archive"]]
    }


@app.post("/api/session/{session_id}/end")
def session_end(session_id: str):
    _session(session_id)
    return sessions.report(sessions.end(session_id)["id"])


@app.delete("/api/session/{session_id}")
def session_delete(session_id: str):
    learn.forget_urls(sessions.urls_only_in(session_id))
    if not sessions.delete(session_id):
        raise HTTPException(404, "세션을 찾을 수 없어요.")
    return {"deleted": session_id}


@app.get("/api/session/{session_id}/report")
def session_report(session_id: str):
    _session(session_id)
    return sessions.report(session_id)


@app.post("/api/session/{session_id}/roadmap")
def session_roadmap_step(session_id: str, req: StepRequest):
    steps = sessions.mark_step(session_id, req.index, req.done)
    if steps is None:
        raise HTTPException(404, "세션이나 단계를 찾을 수 없어요.")
    return {"steps": steps}


@app.get("/api/session/{session_id}/export.md", response_class=PlainTextResponse)
def session_export(session_id: str):
    s = _session(session_id)
    lines = [f"# {s['query']}", "", f"- 시작: {s['started_at']}", f"- 검색어: {', '.join(s['queries']) or s['query']}", ""]
    if s["roadmap"]:
        lines += ["## 검색 로드맵", ""]
        lines += [f"- [{'x' if st['done'] else ' '}] {st['title']} — `{st['search_query']}`" for st in s["roadmap"]]
        lines.append("")
    rep = s.get("learning_report")
    if rep:
        cite = lambda it: " " + "".join(f"[{n}]" for n in it["sources"])
        lines += ["## 학습 리포트 — " + rep["title"], "", f"> {rep['tldr']}", ""]
        blocks = [("핵심 정리", "key_points", None), ("공통점", "common", None), ("차이점", "differences", None),
                  ("실행 계획", "action_plan", "step"), ("주의할 점", "cautions", None), ("용어", "glossary", "term")]
        for head, key, label in blocks:
            if rep.get(key):
                lines += [f"### {head}", ""]
                lines += [f"- {'**' + it[label] + '** — ' if label else ''}{it['text']}{cite(it)}" for it in rep[key]]
                lines.append("")
        if rep.get("open_questions"):
            lines += ["### 더 알아볼 것", ""] + [f"- {q}" for q in rep["open_questions"]] + [""]
        if rep.get("quiz"):
            lines += ["### 이해 확인", ""]
            for i, q in enumerate(rep["quiz"], 1):
                lines.append(f"{i}. {q['question']}")
                lines += [f"   - {'✅ ' if j == q['answer'] else ''}{c}" for j, c in enumerate(q["choices"])]
            lines.append("")
    if s.get("qa"):
        lines += ["## 담은 글에게 물은 것", ""]
        for qa in s["qa"]:
            lines += [f"**Q. {qa['question']}**", "", qa["answer"], ""]
    lines += ["## 담은 글", ""]
    for n, a in enumerate(s["archive"], 1):
        lines += [f"### [{n}] {a['title']}", "", a["url"], ""]
        lines += [f"- {x}" for x in a.get("summary", [])]
        if a.get("keywords"):
            lines.append(f"- 키워드: {', '.join(a['keywords'])}")
        if a.get("memo"):
            lines.append(f"- 메모: {a['memo']}")
        lines.append("")
    return "\n".join(lines)


class SearchItem(BaseModel):
    id: str
    title: str
    snippet: str = ""
    url: str = ""
    is_ad: bool = False


class FilterRequest(BaseModel):
    query: str
    items: list[SearchItem]
    session_id: str | None = None


class DeepRequest(BaseModel):
    intent: str
    items: list[SearchItem]
    session_id: str | None = None


@app.post("/api/focus/filter")
def focus_filter(req: FilterRequest):
    if not req.items:
        raise HTTPException(400, "검색 결과가 없습니다.")
    items = [it.model_dump() for it in req.items[:30]]
    sid = req.session_id if req.session_id and sessions.get(req.session_id) else None
    seen = sessions.seen_urls(sid, req.query) if sid else set()
    res = focus.filter_results(req.query, items, session_id=sid, seen=seen)
    if sid:
        sessions.record_filter(sid, req.query, res["intent"], items, res["results"])
    return res


@app.post("/api/focus/deep")
def focus_deep(req: DeepRequest):
    if not req.items:
        return {"results": [], "calls": {}, "elapsed_ms": 0}
    res = focus.judge_deep(req.intent, [it.model_dump() for it in req.items[:10]], session_id=req.session_id)
    if req.session_id:
        sessions.record_deep(req.session_id, res["results"])
    return res


class RoadmapItem(BaseModel):
    title: str
    snippet: str = ""


class RoadmapRequest(BaseModel):
    query: str
    intent: str = ""
    items: list[RoadmapItem] = []
    session_id: str | None = None


@app.post("/api/focus/roadmap")
def focus_roadmap(req: RoadmapRequest):
    if req.session_id:
        existing = _session(req.session_id)["roadmap"]
        if existing:
            return {"steps": existing, "calls": {}, "cached": True}
    try:
        res = focus.roadmap(req.query, req.intent or req.query, [it.model_dump() for it in req.items], req.session_id)
    except ValueError as e:
        raise HTTPException(502, str(e))
    if req.session_id:
        sessions.set_roadmap(req.session_id, res["steps"])
    return res


class ArchiveRequest(BaseModel):
    session_id: str
    url: str = Field(min_length=1)
    title: str = ""
    text: str = Field(min_length=1)
    memo: str = ""


class MemoRequest(BaseModel):
    session_id: str
    url: str
    memo: str = ""


@app.post("/api/archive")
def archive_add(req: ArchiveRequest):
    _session(req.session_id)
    text = req.text[:6000]
    summary = sessions.cached_summary(req.url)
    cached = summary is not None
    if not cached:
        summary = focus.summarize_article(req.title, text, session_id=req.session_id)
    entry = {"url": req.url, "title": req.title or req.url, "text": text, "memo": req.memo}
    item = sessions.add_archive(req.session_id, entry, summary)
    return {k: v for k, v in item.items() if k != "text"} | {"cached": cached}


@app.patch("/api/archive")
def archive_memo(req: MemoRequest):
    item = sessions.update_memo(req.session_id, req.url, req.memo)
    if not item:
        raise HTTPException(404, "담은 글을 찾을 수 없어요.")
    return {k: v for k, v in item.items() if k != "text"}


@app.delete("/api/archive")
def archive_remove(session_id: str, url: str):
    if not sessions.remove_archive(session_id, url):
        raise HTTPException(404, "담은 글을 찾을 수 없어요.")
    return {"deleted": url}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)


@app.post("/api/session/{session_id}/ask")
def session_ask(session_id: str, req: AskRequest):
    session = _session(session_id)
    on_done = lambda q, a, src, found: sessions.add_qa(session_id, q, a, src, found)
    return StreamingResponse(learn.ask_stream(session, req.question.strip(), on_done), media_type="text/plain; charset=utf-8")


@app.post("/api/session/{session_id}/learning-report")
def session_learning_report(session_id: str):
    session = _session(session_id)
    if not session["archive"]:
        raise HTTPException(400, "리포트를 만들려면 글을 1개 이상 담아 주세요.")
    try:
        data = learn.learning_report(session)
    except ValueError as e:
        raise HTTPException(502, str(e))
    sessions.set_learning_report(session_id, data)
    return data


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
