"""API 키가 제대로 동작하는지 한 번 호출해본다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm

reply = llm.chat(
    [{"role": "user", "content": "한 문장으로 인사해줘."}],
    max_tokens=50,
    tag="check_api",
)
print("✅ 연결 성공:", reply)
