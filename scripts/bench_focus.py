"""집중 검색 판별: 우리 파이프라인 vs 전부 HCX-007 — 같은 결과 묶음으로 실측 비교."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import focus, llm, sessions  # noqa: E402

DEFAULT_REQ = ROOT / "scripts" / "fixtures" / "compal_demo.json"


def _log_offset() -> int:
    return llm.USAGE_LOG.stat().st_size if llm.USAGE_LOG.exists() else 0


def _usage_since(offset: int) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    with llm.USAGE_LOG.open(encoding="utf-8") as f:
        f.seek(offset)
        for line in f:
            r = json.loads(line)
            t = totals.setdefault(r["model"], {"calls": 0, "tokens": 0})
            t["calls"] += 1
            t["tokens"] += r["total_tokens"]
    return totals


def _reset_cache():
    focus._cache.clear()
    focus._intent_cache.clear()
    focus._deep_cache.clear()


def ours(query, items):
    """규칙 → 임베딩 → DASH-002 → 애매만 HCX-007 (확장 프로그램과 같은 순서)"""
    started = time.perf_counter()
    res = focus.filter_results(query, items)
    first = time.perf_counter() - started
    final = {r["id"]: ("duplicate" if r["duplicate_of"] else r["verdict"]) for r in res["results"]}
    stages = res["stages"]
    unsure = [r["id"] for r in res["results"] if r["verdict"] == "unsure" and not r["duplicate_of"]]
    if unsure:
        deep = focus.judge_deep(res["intent"], [it for it in items if it["id"] in unsure])
        for r in deep["results"]:
            final[r["id"]] = r["verdict"]
        stages = {**stages, "fast": stages["fast"] - len(unsure), "deep": len(unsure)}
    return final, stages, first


def all_deep(query, items):
    """모든 결과를 HCX-007 한 번에 (의도 추론은 같게)"""
    intent = focus.infer_intent(query)["intent"]
    deep = focus.judge_deep(intent, items)
    return {r["id"]: r["verdict"] for r in deep["results"]}, {"deep": len(items)}, None


def measure(fn, query, items):
    _reset_cache()
    offset = _log_offset()
    started = time.perf_counter()
    verdicts, stages, first = fn(query, items)
    seconds = time.perf_counter() - started
    usage = _usage_since(offset)
    return {
        "verdicts": verdicts,
        "stages": stages,
        "seconds": seconds,
        "first_seconds": seconds if first is None else first,
        "tokens": sum(u["tokens"] for u in usage.values()),
        "usage": usage,
    }


def _accuracy(verdicts, items):
    graded = [it for it in items if it.get("expected")]
    if not graded:
        return None
    hits = sum(
        verdicts[it["id"]] == it["expected"] or (it["expected"] == "duplicate" and verdicts[it["id"]] == "experience")
        for it in graded
    )
    return hits / len(graded)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REQ
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    req = json.loads(path.read_text(encoding="utf-8"))
    query, items = req["query"], req["items"]
    print(f"검색어: {query} / 결과 {len(items)}개 / {runs}회 반복\n")

    summary = {}
    for name, fn in [("ours", ours), ("all_deep", all_deep)]:
        rows = [measure(fn, query, items) for _ in range(runs)]
        for n, row in enumerate(rows, 1):
            labels = {}
            for v in row["verdicts"].values():
                labels[v] = labels.get(v, 0) + 1
            print(f"[{name} #{n}] 첫 판정 {row['first_seconds']:.1f}s / 전체 {row['seconds']:.1f}s  토큰 {row['tokens']}  모델 {row['usage']}")
            print(f"    판정 {labels}  단계 {row['stages']}")
        acc = [a for a in (_accuracy(r["verdicts"], items) for r in rows) if a is not None]
        summary[name] = {
            "seconds": round(sum(r["seconds"] for r in rows) / runs, 1),
            "first_seconds": round(sum(r["first_seconds"] for r in rows) / runs, 1),
            "tokens": round(sum(r["tokens"] for r in rows) / runs),
            "deep_tokens": round(sum(r["usage"].get(focus.DEEP_MODEL, {}).get("tokens", 0) for r in rows) / runs),
            "deep_calls": round(sum(r["usage"].get(focus.DEEP_MODEL, {}).get("calls", 0) for r in rows) / runs, 1),
            "accuracy": round(sum(acc) / len(acc), 2) if acc else None,
        }

    a, b = summary["ours"], summary["all_deep"]
    print("\n| 방식 | 첫 판정 | 전체 완료 | 전체 토큰 | 그중 HCX-007 | HCX-007 호출 | 정답률 |")
    print("|---|---|---|---|---|---|---|")
    for label, s in [("우리 파이프라인", a), ("전부 HCX-007", b)]:
        acc = f"{s['accuracy']:.0%}" if s["accuracy"] is not None else "-"
        print(
            f"| {label} | {s['first_seconds']}s | {s['seconds']}s | {s['tokens']} | {s['deep_tokens']} "
            f"| {s['deep_calls']} | {acc} |"
        )

    record = {
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "query": query,
        "items": len(items),
        "runs": runs,
        "ours": a,
        "all_deep": b,
    }
    sessions.BENCH_FILE.parent.mkdir(exist_ok=True)
    sessions.BENCH_FILE.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {sessions.BENCH_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
