"""HyperCLOVA X 호출 래퍼."""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_URL = "https://clovastudio.stream.ntruss.com/v1/openai/"
DEFAULT_MODEL = os.getenv("CLOVA_MODEL", "HCX-005")
USAGE_LOG = Path(__file__).resolve().parent.parent / "logs" / "usage.jsonl"

MAX_OUT = {"HCX-DASH-002": 4_096, "HCX-005": 4_096, "HCX-007": 32_768}
REASONING_MIN_TOKENS = 2_048

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        load_dotenv(override=True)
        api_key = os.getenv("CLOVA_API_KEY")
        if not api_key:
            raise RuntimeError("CLOVA_API_KEY가 없습니다. .env 파일을 확인하세요.")
        _client = OpenAI(api_key=api_key, base_url=BASE_URL)
    return _client


def _log_usage(model: str, usage, tag: str | None, elapsed_ms: int) -> None:
    if usage is None:
        return
    USAGE_LOG.parent.mkdir(exist_ok=True)
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tag": tag,
        "model": model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": usage.total_tokens,
        "elapsed_ms": elapsed_ms,
    }
    with USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _options(model: str, max_tokens: int, kwargs: dict) -> dict:
    if model == "HCX-007":
        if "response_format" in kwargs:
            kwargs["reasoning_effort"] = "none"
        elif kwargs.get("reasoning_effort") != "none":
            max_tokens = max(max_tokens, REASONING_MIN_TOKENS)
    else:
        kwargs.pop("reasoning_effort", None)
    kwargs["max_completion_tokens"] = min(max_tokens, MAX_OUT.get(model, 4_096))
    return kwargs


def chat(messages: list[dict], model: str | None = None, max_tokens: int = 512, tag: str | None = None, **kwargs) -> str:
    model = model or DEFAULT_MODEL
    started = time.perf_counter()
    resp = get_client().chat.completions.create(model=model, messages=messages, **_options(model, max_tokens, kwargs))
    _log_usage(model, resp.usage, tag, int((time.perf_counter() - started) * 1000))
    return resp.choices[0].message.content


def stream(messages: list[dict], model: str | None = None, max_tokens: int = 512, tag: str | None = None, **kwargs):
    model = model or DEFAULT_MODEL
    started = time.perf_counter()
    resp = get_client().chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        **_options(model, max_tokens, kwargs),
    )
    usage = None
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
        if getattr(chunk, "usage", None):
            usage = chunk.usage
    _log_usage(model, usage, tag, int((time.perf_counter() - started) * 1000))


def embed(text: str, model: str = "bge-m3", tag: str | None = None) -> list[float]:
    started = time.perf_counter()
    resp = get_client().embeddings.create(model=model, input=text, encoding_format="float")
    _log_usage(model, resp.usage, tag, int((time.perf_counter() - started) * 1000))
    return resp.data[0].embedding
