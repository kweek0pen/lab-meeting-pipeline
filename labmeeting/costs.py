"""API 비용 계산. 가격은 config/pricing.json 에서 읽는다 (코드에 하드코딩하지 않음)."""

from __future__ import annotations

import json
from pathlib import Path

from .settings import CONFIG_DIR

PRICING_PATH = CONFIG_DIR / "pricing.json"


def load_pricing(path: Path = PRICING_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def transcription_cost(model: str, billed_seconds: float, pricing: dict | None = None) -> dict:
    pricing = pricing if pricing is not None else load_pricing()
    per_min = pricing.get("transcription_usd_per_minute", {}).get(model)
    minutes = billed_seconds / 60
    return {
        "model": model,
        "billed_minutes": round(minutes, 2),
        "usd": round(per_min * minutes, 4) if per_min is not None else None,
        "basis": "estimate",  # 분 단위 공시 가격 × 전송한 오디오 길이(겹침 포함) — 청구액과 다를 수 있음
        "note": None if per_min is not None else f"config/pricing.json 에 '{model}' 가격이 없어 계산하지 못함",
    }


def llm_cost(model: str, input_tokens: int, output_tokens: int, pricing: dict | None = None) -> dict:
    pricing = pricing if pricing is not None else load_pricing()
    price = pricing.get("llm_usd_per_million_tokens", {}).get(model)
    usd = None
    if price:
        usd = round(input_tokens / 1e6 * price["input"] + output_tokens / 1e6 * price["output"], 4)
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": usd,
        "basis": "usage",  # API 가 돌려준 실제 토큰 수 × 가격표
        "note": None if price else f"config/pricing.json 에 '{model}' 가격이 없어 계산하지 못함",
    }


def add_llm_costs(a: dict | None, b: dict) -> dict:
    """재시도로 여러 번 호출한 경우 비용을 합산."""
    if not a:
        return b
    merged = dict(b)
    merged["input_tokens"] = a["input_tokens"] + b["input_tokens"]
    merged["output_tokens"] = a["output_tokens"] + b["output_tokens"]
    merged["usd"] = None if a["usd"] is None or b["usd"] is None else round(a["usd"] + b["usd"], 4)
    return merged
