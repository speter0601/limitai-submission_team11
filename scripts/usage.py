"""logs/usage.jsonl 을 집계해 기능(tag)별 / 모델별 토큰 사용량과 평균 응답 시간을 보여준다."""
import json
from collections import defaultdict
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "usage.jsonl"

if not LOG.exists():
    print("아직 기록된 사용량이 없습니다.")
    raise SystemExit

totals = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0, "total": 0, "ms": 0, "timed": 0})
for line in LOG.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    tag = (r["tag"] or "").split("@")[0] or None
    t = totals[(tag, r["model"])]
    t["calls"] += 1
    t["prompt"] += r["prompt_tokens"]
    t["completion"] += r["completion_tokens"]
    t["total"] += r["total_tokens"]
    if r.get("elapsed_ms") is not None:
        t["ms"] += r["elapsed_ms"]
        t["timed"] += 1


def avg(n, d):
    return f"{n / d:.0f}" if d else "-"


print(f"{'tag':<16}{'model':<14}{'calls':>6}{'prompt':>9}{'compl.':>9}{'total':>9}{'tok/call':>10}{'ms/call':>9}")
for (tag, model), t in sorted(totals.items(), key=lambda x: -x[1]["total"]):
    print(
        f"{str(tag):<16}{model:<14}{t['calls']:>6}{t['prompt']:>9}{t['completion']:>9}{t['total']:>9}"
        f"{avg(t['total'], t['calls']):>10}{avg(t['ms'], t['timed']):>9}"
    )
print(f"\n전체 토큰: {sum(t['total'] for t in totals.values())}")
