"""집중 검색 — 검색 이후: 담은 글에게 묻기와 최종 학습 리포트."""
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app import llm
from app.focus import DEEP_MODEL, FAST_MODEL, _cosine, _json_object, _strings, _tag
from app.sessions import DATA_DIR

VECTOR_FILE = DATA_DIR / "vectors.json"
CHUNK_CHARS = 700
MAX_CHUNKS_PER_POST = 8
TOP_CHUNKS = 4
NOT_FOUND_SIMILARITY = 0.35
END_MARK = "\n\u001e"
NOT_FOUND_SENTENCE = "담은 글에는 그 내용이 없어요."

_vec_lock = threading.Lock()


def _chunks(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if buf and len(buf) + len(p) > CHUNK_CHARS:
            chunks.append(buf)
            buf = ""
        buf = f"{buf}\n{p}".strip()
    if buf:
        chunks.append(buf)
    return [c[:CHUNK_CHARS * 2] for c in chunks][:MAX_CHUNKS_PER_POST]


def _load_vectors() -> dict:
    return json.loads(VECTOR_FILE.read_text(encoding="utf-8")) if VECTOR_FILE.exists() else {}


def _post_vectors(posts: list[dict], session_id: str) -> dict:
    """URL별 조각·벡터. 없는 글만 새로 임베딩한다 (병렬)."""
    with _vec_lock:
        store = _load_vectors()
    todo = [(p["url"], c) for p in posts if p["url"] not in store for c in _chunks(p.get("text", ""))]
    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            vecs = list(pool.map(lambda t: llm.embed(t[1], tag=_tag("ask-embed", session_id)), todo))
        with _vec_lock:
            store = _load_vectors()
            for (url, chunk), vec in zip(todo, vecs):
                entry = store.setdefault(url, {"chunks": [], "vecs": []})
                entry["chunks"].append(chunk)
                entry["vecs"].append([round(x, 5) for x in vec])
            DATA_DIR.mkdir(exist_ok=True)
            VECTOR_FILE.write_text(json.dumps(store), encoding="utf-8")
    return store


def _search(posts: list[dict], question: str, session_id: str) -> tuple[list[dict], float]:
    store = _post_vectors(posts, session_id)
    qvec = llm.embed(question, tag=_tag("ask-embed", session_id))
    scored = []
    for n, p in enumerate(posts, 1):
        entry = store.get(p["url"], {"chunks": [], "vecs": []})
        for chunk, vec in zip(entry["chunks"], entry["vecs"]):
            scored.append({"n": n, "title": p["title"], "url": p["url"], "chunk": chunk, "score": _cosine(qvec, vec)})
    scored.sort(key=lambda s: -s["score"])
    return scored[:TOP_CHUNKS], (scored[0]["score"] if scored else 0.0)


def ask_stream(session: dict, question: str, on_done):
    """첫 줄은 메타 JSON(근거 글·찾았는지), 그다음부터 답 글자. 끝나면 on_done(기록)."""
    posts = session["archive"]
    sid = session["id"]
    if not posts:
        yield json.dumps({"found": False, "sources": [], "reason": "no_posts"}, ensure_ascii=False) + "\n"
        yield "아직 담은 글이 없어요. 검색하면서 글을 담으면 그 글을 근거로 답해 드려요."
        return
    hits, best = _search(posts, question, sid)
    sources = sorted({(h["n"], h["title"], h["url"]) for h in hits})
    if best < NOT_FOUND_SIMILARITY:
        yield json.dumps({"found": False, "sources": [], "best": round(best, 3)}, ensure_ascii=False) + "\n"
        answer = "담은 글에서는 이 질문의 답을 찾지 못했어요. 이 질문으로 검색해서 글을 더 모아 볼까요?"
        yield answer
        on_done(question, answer, [], False)
        return

    yield json.dumps(
        {"found": True, "best": round(best, 3), "sources": [{"n": n, "title": t, "url": u} for n, t, u in sources]},
        ensure_ascii=False,
    ) + "\n"
    excerpts = "\n\n".join(f"[{h['n']}] {h['title']}\n{h['chunk']}" for h in hits)
    parts = []
    for piece in llm.stream(
        [
            {
                "role": "system",
                "content": (
                    "사용자가 담은 글의 발췌만 근거로 질문에 답한다. 3~5문장, 해요체.\n"
                    "모든 문장마다 끝에 근거 글 번호를 붙인다. 번호는 발췌에 있는 번호만 쓴다.\n"
                    "예: 엑셀은 계산작업부터 푸는 게 좋아요 [2]. 시험 시간은 과목당 45분이에요 [2].\n"
                    f"발췌가 질문과 다른 주제라 답할 수 없으면 다른 말 없이 '{NOT_FOUND_SENTENCE}' 한 문장만 쓴다."
                ),
            },
            {"role": "user", "content": f"발췌:\n{excerpts}\n\n질문: {question}"},
        ],
        model=FAST_MODEL,
        max_tokens=500,
        temperature=0.2,
        tag=_tag("ask", sid),
    ):
        parts.append(piece)
        yield piece
    answer = "".join(parts)
    found = not (re.search(r"담은 글에는.*없어요", answer) and not re.search(r"\[\d+\]", answer))
    valid = {n for n, _, _ in sources}
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)} & valid)
    on_done(question, answer, (cited or sorted(valid)) if found else [], found)
    yield END_MARK + json.dumps({"found": found, "cited": cited}, ensure_ascii=False)


def _claims(extra: dict | None = None) -> dict:
    props = {"text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "integer"}}}
    props.update(extra or {})
    return {"type": "array", "items": {"type": "object", "properties": props, "required": list(props)}}


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "tldr": {"type": "string"},
        "key_points": _claims(),
        "common": _claims(),
        "differences": _claims(),
        "action_plan": _claims({"step": {"type": "string"}}),
        "cautions": _claims(),
        "glossary": _claims({"term": {"type": "string"}}),
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "tldr", "key_points", "common", "differences", "action_plan", "cautions", "glossary", "open_questions"],
}
LIMITS = {"key_points": 5, "common": 4, "differences": 4, "action_plan": 5, "cautions": 3, "glossary": 6}
CITE_RE = re.compile(r"\s*\[\d+\]")


def _checked(items, count: int, limit: int, min_sources: int = 1) -> tuple[list[dict], int]:
    """출처가 있고 모두 범위 안인 항목만. 문장 속 [n]은 지우고 출처는 sources로만 (형식은 코드)."""
    kept, dropped = [], 0
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            dropped += 1
            continue
        text = CITE_RE.sub("", str(it.get("text", ""))).strip()
        nums = sorted({n for n in it.get("sources", []) if isinstance(n, int)})
        if text and len(nums) >= min_sources and all(1 <= n <= count for n in nums):
            kept.append({**{k: str(v).strip() for k, v in it.items() if k not in ("text", "sources")}, "text": text, "sources": nums})
        else:
            dropped += 1
    return kept[:limit], dropped


def _sources_text(posts: list[dict]) -> str:
    return "\n\n".join(
        f"[{n}] {p['title']}\n요약: {' / '.join(p.get('summary', []))}\n"
        + (f"사용자 메모: {p['memo']}\n" if p.get("memo") else "")
        + f"본문 일부: {p.get('text', '')[:1500]}"
        for n, p in enumerate(posts, 1)
    )


def _report_call(session: dict, posts: list[dict]) -> dict:
    asked = [q["question"] for q in session.get("qa", []) if q.get("found")][-6:]
    done = [s["title"] for s in session["roadmap"] if s["done"]]
    context = (
        f"학습 주제: {session['query']}\n검색 의도: {session.get('intent') or session['query']}\n"
        + (f"사용자가 궁금해한 질문: {' / '.join(asked)}\n" if asked else "")
        + (f"이미 찾아본 단계: {', '.join(done)}\n" if done else "")
    )
    text = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "사용자가 검색하며 직접 담은 글만 근거로 학습 리포트를 쓴다. 글에 없는 사실은 쓰지 않는다. 해요체.\n"
                    "모든 text는 완결된 해요체 한 문장이다.\n"
                    "title: 리포트 제목(주제를 담아 20자 이내). tldr: 한 줄 결론(해요체 2문장 이내).\n"
                    "key_points: 꼭 알아야 할 핵심 3~5개. common: 두 개 이상의 글이 함께 말하는 점. "
                    "differences: 글마다 다른 점·엇갈리는 주장. "
                    "action_plan: 사용자가 따라 할 순서 3~5단계. step은 그 단계의 짧은 이름(10자 이내), text는 할 일. "
                    "cautions: 주의할 점·흔한 실수. "
                    "glossary: 글에 나온 낯선 용어. term은 용어 이름만(예: 정규화), text는 그 뜻 한 문장. "
                    "open_questions: 학습 주제와 관련 있지만 담은 글로는 답이 안 되는 것을, 다음에 네이버에 넣을 검색어로 2~3개.\n"
                    "각 항목의 sources에는 근거가 된 자료 번호를 모두 넣는다. 번호는 아래 자료 번호만 쓴다. "
                    "사용자 메모와 질문은 무엇을 강조할지 정하는 데만 쓰고 근거로 쓰지 않는다."
                ),
            },
            {"role": "user", "content": f"{context}\n자료:\n{_sources_text(posts)}"},
        ],
        model=DEEP_MODEL,
        max_tokens=3_500,
        reasoning_effort="none",
        response_format={"type": "json_schema", "json_schema": {"name": "learning_report", "schema": REPORT_SCHEMA}},
        tag=_tag("learning-report", session["id"]),
    )
    return _json_object(text)


QUIZ_FORMAT = (
    '{"quiz": [{"question": "질문", "answer": "정답 보기 문장", "wrong": ["오답1", "오답2", "오답3"], '
    '"explain": "정답인 이유 한 문장", "source": 근거 자료 번호}]}'
)


def _quiz_call(session: dict, posts: list[dict]) -> list[dict]:
    text = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "자료 내용만으로 이해를 확인하는 4지선다 문제 3개를 만든다. 자료에 답이 분명히 있는 것만 묻는다. "
                    "answer는 자료에 근거한 정답 하나, wrong은 그럴듯하지만 자료와 다른 오답 3개. "
                    "다른 말 없이 JSON 한 줄만 출력한다.\n형식: " + QUIZ_FORMAT
                ),
            },
            {"role": "user", "content": f"학습 주제: {session['query']}\n\n자료:\n{_sources_text(posts)}"},
        ],
        model=FAST_MODEL,
        max_tokens=900,
        temperature=0.3,
        tag=_tag("quiz", session["id"]),
    )
    raw = _json_object(text).get("quiz")
    quiz = []
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        answer, wrong, source = q.get("answer"), _strings(q.get("wrong"), 3), q.get("source")
        if (
            isinstance(q.get("question"), str) and isinstance(answer, str) and answer.strip()
            and len(wrong) == 3 and answer.strip() not in wrong
            and isinstance(source, int) and 1 <= source <= len(posts)
        ):
            choices = [answer.strip(), *wrong]
            random.shuffle(choices)
            quiz.append({"question": q["question"].strip(), "choices": choices, "answer": choices.index(answer.strip()),
                         "explain": str(q.get("explain", "")).strip(), "source": source})
    return quiz[:3]


def learning_report(session: dict) -> dict:
    posts = session["archive"][:8]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        report_job = pool.submit(_report_call, session, posts)
        quiz_job = pool.submit(_quiz_call, session, posts)
        data = report_job.result()
        try:
            quiz = quiz_job.result()
        except Exception:
            quiz = []

    count, dropped, out = len(posts), 0, {}
    for key, limit in LIMITS.items():
        out[key], d = _checked(data.get(key), count, limit, min_sources=2 if key == "common" else 1)
        dropped += d
    for it in out["action_plan"]:
        it["text"] = re.sub(r"^\s*\d+\s*단계\s*[:.)]?\s*", "", it["text"])
    for it in out["glossary"]:
        if len(it.get("term", "")) > len(it["text"]):
            it["term"], it["text"] = it["text"], it["term"]
    if not out["key_points"] and not out["action_plan"]:
        raise ValueError("출처가 확인되는 내용을 만들지 못했어요. 다시 시도해 주세요.")
    return {
        "title": str(data.get("title") or f"{session['query']} 학습 리포트").strip(),
        "tldr": CITE_RE.sub("", str(data.get("tldr", ""))).strip(),
        **out,
        "open_questions": _strings(data.get("open_questions"), 3),
        "quiz": quiz,
        "dropped": dropped,
        "sources": [{"n": n, "title": p["title"], "url": p["url"], "memo": p.get("memo", "")} for n, p in enumerate(posts, 1)],
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


def forget_urls(urls: set[str]) -> None:
    """세션을 지울 때, 다른 세션이 안 쓰는 글의 본문 조각도 지운다."""
    with _vec_lock:
        store = _load_vectors()
        if any(u in store for u in urls):
            VECTOR_FILE.write_text(json.dumps({u: v for u, v in store.items() if u not in urls}), encoding="utf-8")
