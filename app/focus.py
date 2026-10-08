"""집중 검색 — 네이버 검색 결과를 검색 의도 기준으로 거르고, 담은 글을 정리한다."""
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor

from app import llm

FAST_MODEL = "HCX-DASH-002"
DEEP_MODEL = "HCX-007"
DEEP_EFFORT = "low"

UNRELATED_SIMILARITY = 0.38
DUPLICATE_SIMILARITY = 0.92
PROMO_WORDS = ("원고료", "소정의", "제공받아", "협찬", "광고")
PROMO_EXCEPTIONS = ("광고 아님", "광고아님", "광고 아닌", "광고 없", "광고없", "내돈내산")

FAST_LABELS = {"경험": "experience", "정보": "experience", "홍보": "promo", "샛길": "offtrack", "애매": "unsure"}
DEEP_LABELS = {k: v for k, v in FAST_LABELS.items() if k != "애매"}
UNSURE_REASON = "판단이 어려워 다시 볼게요"

_cache: dict[str, dict] = {}
_intent_cache: dict[str, dict] = {}
_deep_cache: dict[tuple[str, str], dict] = {}


def _tag(name: str, session_id: str | None) -> str:
    """사용량 로그 태그. 세션이 있으면 '이름@세션ID' → 세션 리포트에서 집계."""
    return f"{name}@{session_id}" if session_id else name


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _item_text(item: dict) -> str:
    return f"{item.get('title', '')}. {item.get('snippet', '')}"[:500]


def _numbered(items: list[dict]) -> str:
    return "\n".join(
        f"[{i}] 제목: {it.get('title', '')}\n    요약: {it.get('snippet', '')[:200]}"
        for i, it in enumerate(items)
    )


def _json_object(text: str | None) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _strings(value, limit: int) -> list[str]:
    return [v.strip() for v in value if isinstance(v, str) and v.strip()][:limit] if isinstance(value, list) else []


def infer_intent(query: str, session_id: str | None = None) -> dict:
    if query in _intent_cache:
        return _intent_cache[query]
    text = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "사용자의 네이버 검색어에서 검색 의도를 추론한다. 다른 말 없이 JSON 한 줄만 출력한다.\n"
                    '형식: {"intent": "누가 무엇을 알고 싶은지 한 문장", "chips": ["의도 후보 2~3개, 각 8자 이내"]}\n'
                    '예: 검색어 "파이썬" → {"intent": "파이썬을 배우거나 쓰려는 사람이 기초와 활용법을 알고 싶음", '
                    '"chips": ["기초 문법", "설치 방법", "활용 사례"]}'
                ),
            },
            {"role": "user", "content": query},
        ],
        model=FAST_MODEL,
        max_tokens=200,
        temperature=0.2,
        tag=_tag("focus-intent", session_id),
    )
    data = _json_object(text)
    intent = data.get("intent") if isinstance(data.get("intent"), str) else None
    if not intent and text and "{" not in text:
        intent = re.sub(r"\s*\(.*?\)", "", text.strip().splitlines()[0]).strip()[:80] or None
    result = {
        "intent": intent or f"'{query}'에 대해 알고 싶음",
        "chips": _strings(data.get("chips"), 3),
    }
    _intent_cache[query] = result
    return result


def _rule_reason(item: dict) -> str | None:
    if item.get("is_ad"):
        return "파워링크 광고 영역이에요"
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    for phrase in PROMO_EXCEPTIONS:
        text = text.replace(phrase, "")
    for word in PROMO_WORDS:
        if word in text:
            return f"'{word}' 표시가 있어요"
    return None


def _parse_verdicts(text: str, count: int, labels: dict) -> dict[int, tuple[str, str]]:
    """'번호|라벨|이유' 줄들을 {번호: (verdict, 이유)}로 바꾼다. 형식이 틀린 줄은 버린다."""
    verdicts = {}
    for line in (text or "").splitlines():
        parts = [p.strip(" []") for p in line.split("|")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        idx, label, reason = int(parts[0]), parts[1], parts[2]
        if 0 <= idx < count and label in labels and reason:
            verdicts[idx] = (labels[label], reason)
    return verdicts


JUDGE_RULES = (
    "판정 기준은 하나다: 실제 사람이 직접 겪었거나 검증 가능한 정보인가.\n"
    "라벨은 넷 중 하나:\n"
    "- 경험: 직접 겪은 후기, 검증 가능한 정보, 같은 주제를 묻거나 답하는 질문 글\n"
    "- 홍보: 학원·강의·교재·상품 판매나 수강생 모집. '솔직 후기'로 포장했어도 특정 강의·학원을 권하거나 "
    "모집·할인·상담 링크가 있으면 홍보. 합격 후기라도 수강 신청·상담 안내가 붙으면 홍보\n"
    "- 샛길: 다른 시험·다른 분야처럼 주제 자체가 다른 글\n"
    "- 애매: 위 셋으로 판단하기 어려움\n"
    "같은 대상(같은 시험·기술·목표)을 다루면 글 종류가 후기·질문·정리 중 무엇이든 샛길이 아니다. "
    "질문 형식(~하나요?, ~될까요?)이어도 같은 대상을 묻는 글은 경험이다.\n"
    "이유는 사용자에게 보여 줄 20자 이내 해요체 한 문장.\n\n"
    "예시 입력:\n"
    "검색 의도: 비전공자가 AI 개발자가 되는 공부 순서를 알고 싶음\n"
    "[0] 제목: 비전공자가 AI 개발자가 되려면 어떤 순서로 공부해야 하나요?\n"
    "[1] 제목: 3개월 만에 AI 개발자 취업! 부트캠프 솔직 후기 (수강 신청 링크)\n"
    "[2] 제목: 비전공 2년 차 AI 개발자, 제가 실제로 공부한 순서\n"
    "[3] 제목: 강남역 점심 맛집 모음\n"
    "[4] 제목: 파이썬 독학 6개월이면 AI 개발자 취업 가능할까요?\n"
    "예시 출력:\n"
    "0|경험|같은 고민을 묻는 질문 글이에요\n"
    "1|홍보|후기로 포장한 수강 모집이에요\n"
    "2|경험|직접 공부한 순서를 적었어요\n"
    "3|샛길|검색 주제와 다른 글이에요\n"
    "4|경험|같은 목표를 묻는 질문 글이에요"
)


def _judge_fast(intent: str, items: list[dict], session_id: str | None) -> dict[int, tuple[str, str]]:
    text = llm.chat(
        [
            {
                "role": "system",
                "content": "검색 의도에 비추어 검색 결과를 하나씩 판정한다. 결과마다 한 줄씩, 다른 말 없이 "
                "'번호|라벨|이유' 형식으로 출력한다.\n" + JUDGE_RULES,
            },
            {"role": "user", "content": f"검색 의도: {intent}\n\n{_numbered(items)}"},
        ],
        model=FAST_MODEL,
        max_tokens=60 * len(items) + 80,
        temperature=0.1,
        tag=_tag("focus-judge", session_id),
    )
    return _parse_verdicts(text, len(items), FAST_LABELS)


def _classify(query: str, items: list[dict], session_id: str | None) -> dict:
    """0~2단계. 결과는 캐시해 같은 검색을 다시 열어도 모델을 부르지 않는다."""
    fresh_intent = query not in _intent_cache
    results = [{"id": it.get("id"), "url": it.get("url", ""), "score": None, "duplicate_of": None} for it in items]
    rest = []
    for i, it in enumerate(items):
        reason = _rule_reason(it)
        if reason:
            results[i].update(verdict="promo", reason=reason, stage="rule")
        else:
            rest.append(i)

    with ThreadPoolExecutor(max_workers=8) as pool:
        intent_job = pool.submit(infer_intent, query, session_id)
        vectors = list(pool.map(lambda i: llm.embed(_item_text(items[i]), tag=_tag("focus-embed", session_id)), rest))
        intent = intent_job.result()
    intent_vec = llm.embed(intent["intent"], tag=_tag("focus-embed", session_id)) if rest else []

    originals: list[int] = []
    duplicates: dict[int, int] = {}
    judge = []
    for pos, i in enumerate(rest):
        results[i]["score"] = round(_cosine(intent_vec, vectors[pos]), 3)
        twin = next((o for o in originals if _cosine(vectors[o], vectors[pos]) >= DUPLICATE_SIMILARITY), None)
        if twin is not None:
            duplicates[i] = rest[twin]
            continue
        originals.append(pos)
        if results[i]["score"] < UNRELATED_SIMILARITY:
            results[i].update(verdict="offtrack", reason="검색 의도와 관련이 적어요", stage="embed")
        else:
            judge.append(i)

    if judge:
        verdicts = _judge_fast(intent["intent"], [items[i] for i in judge], session_id)
        for local, i in enumerate(judge):
            verdict, reason = verdicts.get(local, ("unsure", UNSURE_REASON))
            results[i].update(verdict=verdict, reason=reason, stage="fast")

    for i, original in duplicates.items():
        results[i].update(
            verdict=results[original]["verdict"],
            reason=f"{original + 1}번째 글과 내용이 거의 같아요",
            stage="embed",
            duplicate_of=results[original]["id"],
        )

    return {
        **intent,
        "results": results,
        "calls": {FAST_MODEL: int(fresh_intent) + int(bool(judge)), "bge-m3": len(rest) + int(bool(rest))},
    }


def stage_counts(results: list[dict]) -> dict[str, int]:
    counts = {"rule": 0, "embed": 0, "fast": 0, "deep": 0}
    for r in results:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    return counts


def filter_results(query: str, items: list[dict], session_id: str | None = None, seen: set[str] = frozenset()) -> dict:
    started = time.perf_counter()
    cache_key = json.dumps([query, [it.get("url") for it in items]], ensure_ascii=False)
    cached = cache_key in _cache
    if not cached:
        _cache[cache_key] = _classify(query, items, session_id)
    base = _cache[cache_key]
    results = []
    for r in base["results"]:
        deep = _deep_cache.get((base["intent"], r["url"])) if r["verdict"] == "unsure" else None
        if deep:
            r = {**r, "verdict": deep["verdict"], "reason": deep["reason"], "stage": "deep"}
        results.append({**r, "seen": bool(r["url"]) and r["url"] in seen})
    return {
        "query": query,
        "intent": base["intent"],
        "chips": base["chips"],
        "results": results,
        "stages": stage_counts(results),
        "calls": {} if cached else base["calls"],
        "cached": cached,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


def judge_deep(intent: str, items: list[dict], session_id: str | None = None) -> dict:
    started = time.perf_counter()
    todo = [it for it in items if not it.get("url") or (intent, it["url"]) not in _deep_cache]
    if todo:
        _judge_deep_llm(intent, todo, session_id)
    results = [
        {"id": it.get("id"), "url": it.get("url", ""), "stage": "deep",
         **_deep_cache.get((intent, it.get("url", "")), {"verdict": "unsure", "reason": "판단하기 어려운 글이에요"})}
        for it in items
    ]
    return {
        "results": results,
        "calls": {DEEP_MODEL: 1} if todo else {},
        "cached": not todo,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


def _judge_deep_llm(intent: str, items: list[dict], session_id: str | None) -> None:
    text = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "검색 의도에 비추어 검색 결과를 판정한다. 제목과 요약의 말투, 광고성 표현, 주제 일치 여부를 따져 본다.\n"
                    "결과마다 한 줄씩, 다른 말 없이 '번호|라벨|이유' 형식으로 출력한다. 라벨은 경험 / 홍보 / 샛길 중 하나.\n"
                    + JUDGE_RULES.replace("- 애매: 위 셋으로 판단하기 어려움\n", "").replace("넷 중 하나", "셋 중 하나")
                ),
            },
            {"role": "user", "content": f"검색 의도: {intent}\n\n{_numbered(items)}"},
        ],
        model=DEEP_MODEL,
        max_tokens=4_096,
        reasoning_effort=DEEP_EFFORT,
        tag=_tag("focus-deep", session_id),
    )
    verdicts = _parse_verdicts(text, len(items), DEEP_LABELS)
    for i, it in enumerate(items):
        if i in verdicts and it.get("url"):
            _deep_cache[(intent, it.get("url", ""))] = {"verdict": verdicts[i][0], "reason": verdicts[i][1]}


def _roadmap_steps(text: str | None, query: str) -> list[dict]:
    raw = _json_object(text).get("steps")
    steps = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        title, why, q = (str(s.get(k, "")).strip() for k in ("title", "why", "search_query"))
        if title and q and q != query and q not in [x["search_query"] for x in steps]:
            steps.append({"title": title, "why": why, "search_query": q, "done": False})
    return steps


def roadmap(query: str, intent: str, items: list[dict], session_id: str | None = None) -> dict:
    started = time.perf_counter()
    messages = [
        {
            "role": "system",
            "content": (
                "사용자가 검색으로 목표를 이루도록 '다음에 무엇을 검색할지' 순서를 짠다. 사실을 단정하지 말고, "
                "무엇을 찾아 확인해야 하는지만 말한다. 다른 말 없이 JSON 한 줄만 출력한다.\n"
                '형식: {"steps": [{"title": "단계 이름 12자 이내", "why": "이 단계에서 확인할 것, 해요체 한 문장", '
                '"search_query": "네이버에 그대로 넣을 검색어"}]}\n'
                "steps는 정확히 4개. 첫 단계는 지금 검색어와 달라야 한다."
            ),
        },
        {
            "role": "user",
            "content": f"지금 검색어: {query}\n검색 의도: {intent}\n\n지금 검색에서 추천된 글:\n{_numbered(items[:8])}",
        },
    ]
    calls = 0
    steps: list[dict] = []
    for _ in range(2):
        text = llm.chat(messages, model=FAST_MODEL, max_tokens=700, temperature=0.3, tag=_tag("focus-roadmap", session_id))
        calls += 1
        steps = _roadmap_steps(text, query)
        if len(steps) >= 3:
            break
        messages += [
            {"role": "assistant", "content": text or ""},
            {"role": "user", "content": f"단계가 {len(steps)}개뿐이에요. 같은 형식으로 4개를 채워 JSON 전체를 다시 출력하세요."},
        ]
    if len(steps) < 2:
        raise ValueError("로드맵을 만들지 못했어요. 다시 시도해 주세요.")
    return {"steps": steps[:5], "calls": {FAST_MODEL: calls}, "elapsed_ms": int((time.perf_counter() - started) * 1000)}


def summarize_article(title: str, text: str, session_id: str | None = None) -> dict:
    reply = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "블로그 글 본문을 요약한다. 본문에 있는 내용만 쓴다. 다른 말 없이 JSON 한 줄만 출력한다.\n"
                    '형식: {"summary": ["핵심 문장 3개, 각 40자 이내"], "keywords": ["핵심 키워드 3개"]}'
                ),
            },
            {"role": "user", "content": f"제목: {title}\n\n본문:\n{text[:6000]}"},
        ],
        model=FAST_MODEL,
        max_tokens=400,
        temperature=0.2,
        tag=_tag("archive-summary", session_id),
    )
    data = _json_object(reply)
    summary = _strings(data.get("summary"), 3)
    if not summary:
        summary = [l.strip("-•· ") for l in (reply or "").splitlines() if l.strip("-•· ")][:3]
    return {"summary": summary, "keywords": _strings(data.get("keywords"), 3)}
