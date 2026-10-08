"""집중 검색 세션 — 검색 기록·판정·아카이브를 로컬 JSON(data/sessions.json)에만 저장한다."""
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from app import llm

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_FILE = DATA_DIR / "sessions.json"
BENCH_FILE = DATA_DIR / "bench.json"

SECONDS_PER_FILTERED = 90
FILTERED = ("promo", "offtrack")

_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load() -> dict:
    if not DATA_FILE.exists():
        return {"sessions": {}, "summaries": {}}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _save(db: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA_FILE)


@contextmanager
def _edit():
    with _lock:
        db = _load()
        yield db
        _save(db)


def all_sessions() -> list[dict]:
    with _lock:
        db = _load()
    return sorted(db["sessions"].values(), key=lambda s: s["started_at"], reverse=True)


def get(session_id: str) -> dict | None:
    with _lock:
        return _load()["sessions"].get(session_id)


def create(query: str) -> dict:
    session = {
        "id": uuid.uuid4().hex[:12],
        "query": query,
        "started_at": _now(),
        "started_ts": time.time(),
        "ended_at": None,
        "ended_ts": None,
        "intent": "",
        "queries": [],
        "results": {},
        "roadmap": [],
        "archive": [],
        "qa": [],
        "learning_report": None,
    }
    with _edit() as db:
        db["sessions"][session["id"]] = session
    return session


def end(session_id: str) -> dict | None:
    with _edit() as db:
        session = db["sessions"].get(session_id)
        if session and not session["ended_at"]:
            session["ended_at"], session["ended_ts"] = _now(), time.time()
    return session


def delete(session_id: str) -> bool:
    with _edit() as db:
        return db["sessions"].pop(session_id, None) is not None


def seen_urls(session_id: str, query: str) -> set[str]:
    """이 세션의 다른 검색에서 이미 나온 글 + 아카이브에 담은 글"""
    session = get(session_id) or {}
    urls = {url for url, r in session.get("results", {}).items() if r["query"] != query}
    return urls | {a["url"] for a in session.get("archive", [])}


def record_filter(session_id: str, query: str, intent: str, items: list[dict], results: list[dict]) -> None:
    titles = {it["id"]: it.get("title", "") for it in items}
    with _edit() as db:
        session = db["sessions"].get(session_id)
        if not session:
            return
        session["intent"] = session["intent"] or intent
        if query not in session["queries"]:
            session["queries"].append(query)
        for r in results:
            if r.get("url"):
                session["results"][r["url"]] = {
                    "query": query,
                    "title": titles.get(r["id"], ""),
                    "verdict": r["verdict"],
                    "stage": r["stage"],
                    "duplicate": bool(r.get("duplicate_of")),
                }
        for step in session["roadmap"]:
            if step["search_query"] == query:
                step["done"] = True


def record_deep(session_id: str, results: list[dict]) -> None:
    with _edit() as db:
        session = db["sessions"].get(session_id)
        if not session:
            return
        for r in results:
            if r.get("url") in session["results"]:
                session["results"][r["url"]].update(verdict=r["verdict"], stage=r["stage"])


def set_roadmap(session_id: str, steps: list[dict]) -> None:
    with _edit() as db:
        if session_id in db["sessions"]:
            db["sessions"][session_id]["roadmap"] = steps


def mark_step(session_id: str, index: int, done: bool) -> list[dict] | None:
    with _edit() as db:
        session = db["sessions"].get(session_id)
        if not session or not 0 <= index < len(session["roadmap"]):
            return None
        session["roadmap"][index]["done"] = done
        return session["roadmap"]


def cached_summary(url: str) -> dict | None:
    with _lock:
        return _load()["summaries"].get(url)


def add_archive(session_id: str, entry: dict, summary: dict) -> dict | None:
    with _edit() as db:
        session = db["sessions"].get(session_id)
        if not session:
            return None
        db["summaries"][entry["url"]] = summary
        existing = next((a for a in session["archive"] if a["url"] == entry["url"]), None)
        if existing:
            if entry.get("memo"):
                existing["memo"] = entry["memo"]
            return existing
        item = {**entry, **summary, "added_at": _now()}
        session["archive"].append(item)
        return item


def update_memo(session_id: str, url: str, memo: str) -> dict | None:
    with _edit() as db:
        session = db["sessions"].get(session_id) or {}
        item = next((a for a in session.get("archive", []) if a["url"] == url), None)
        if item:
            item["memo"] = memo
        return item


def remove_archive(session_id: str, url: str) -> bool:
    with _edit() as db:
        session = db["sessions"].get(session_id) or {}
        before = len(session.get("archive", []))
        session["archive"] = [a for a in session.get("archive", []) if a["url"] != url]
        return len(session["archive"]) < before


def add_qa(session_id: str, question: str, answer: str, sources: list[int], found: bool) -> None:
    with _edit() as db:
        session = db["sessions"].get(session_id)
        if session:
            session.setdefault("qa", []).append(
                {"question": question, "answer": answer, "sources": sources, "found": found, "asked_at": _now()}
            )


def set_learning_report(session_id: str, data: dict) -> None:
    with _edit() as db:
        if session_id in db["sessions"]:
            db["sessions"][session_id]["learning_report"] = {**data, "created_at": _now()}


def urls_only_in(session_id: str) -> set[str]:
    """이 세션에만 담긴 글 주소 (세션 삭제 때 본문 조각을 함께 지우려고)"""
    with _lock:
        db = _load()
    mine = {a["url"] for a in db["sessions"].get(session_id, {}).get("archive", [])}
    others = {a["url"] for sid, s in db["sessions"].items() if sid != session_id for a in s["archive"]}
    return mine - others


def _usage_for(session_id: str) -> dict[str, dict]:
    """llm.py 사용량 로그에서 이 세션 태그('이름@세션ID')만 모델별로 모은다."""
    totals: dict[str, dict] = {}
    if not llm.USAGE_LOG.exists():
        return totals
    suffix = f"@{session_id}"
    with llm.USAGE_LOG.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not (r.get("tag") or "").endswith(suffix):
                continue
            t = totals.setdefault(r["model"], {"calls": 0, "tokens": 0, "ms": 0})
            t["calls"] += 1
            t["tokens"] += r["total_tokens"]
            t["ms"] += r.get("elapsed_ms") or 0
    return totals


def report(session_id: str) -> dict | None:
    session = get(session_id)
    if not session:
        return None
    results = list(session["results"].values())
    labels = {v: 0 for v in ("experience", "promo", "offtrack", "unsure")}
    stages = {"rule": 0, "embed": 0, "fast": 0, "deep": 0}
    duplicates = 0
    for r in results:
        if r["duplicate"]:
            duplicates += 1
        else:
            labels[r["verdict"]] = labels.get(r["verdict"], 0) + 1
        stages[r["stage"]] = stages.get(r["stage"], 0) + 1
    filtered = labels["promo"] + labels["offtrack"] + duplicates
    usage = _usage_for(session_id)
    tokens = sum(u["tokens"] for u in usage.values())

    comparison = None
    if BENCH_FILE.exists():
        bench = json.loads(BENCH_FILE.read_text(encoding="utf-8"))
        per_item = bench["all_deep"]["tokens"] / bench["items"]
        comparison = {
            "measured": bench,
            "all_deep_tokens_estimate": round(per_item * len(results)),
        }

    end_ts = session["ended_ts"] or time.time()
    return {
        "session_id": session_id,
        "query": session["query"],
        "queries": session["queries"],
        "ended": bool(session["ended_at"]),
        "duration_sec": int(end_ts - session["started_ts"]),
        "results": len(results),
        "labels": labels,
        "duplicates": duplicates,
        "filtered": filtered,
        "archived": len(session["archive"]),
        "roadmap_done": sum(s["done"] for s in session["roadmap"]),
        "roadmap_total": len(session["roadmap"]),
        "saved_sec_estimate": filtered * SECONDS_PER_FILTERED,
        "seconds_per_filtered": SECONDS_PER_FILTERED,
        "stages": stages,
        "usage": usage,
        "tokens": tokens,
        "comparison": comparison,
    }
