from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


@dataclass
class ModelOpinion:
    model_name: str
    success: bool
    latency_ms: float
    prompt_tokens: int | None
    response_tokens: int | None
    tokens_per_second: float | None
    response_text: str
    parsed: dict[str, Any] | None
    error: str | None = None


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


class OllamaClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    def installed_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return [item["name"] for item in response.json().get("models", [])]
        except (httpx.HTTPError, KeyError, TypeError):
            return []

    def warm_models(self, model_names: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
        """Load configured models concurrently without counting warmup as query latency."""
        names = list(model_names or settings.ollama_models)

        def warm(model_name: str) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                response = httpx.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model_name,
                        "stream": False,
                        "think": False,
                        "format": "json",
                        "messages": [{"role": "user", "content": "输出JSON：{\"ok\":true}"}],
                        "keep_alive": settings.model_keep_alive,
                        "options": {
                            "num_ctx": settings.model_context_size,
                            "num_predict": 1,
                            "temperature": 0,
                        },
                    },
                    timeout=max(30.0, settings.model_timeout_seconds * 2),
                )
                response.raise_for_status()
                return {"model": model_name, "success": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
            except (httpx.HTTPError, ValueError) as exc:
                return {
                    "model": model_name, "success": False,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc),
                }

        if not names:
            return []
        with ThreadPoolExecutor(max_workers=len(names)) as executor:
            return list(executor.map(warm, names))

    def chat_json(self, model_name: str, system_prompt: str, user_prompt: str) -> ModelOpinion:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=settings.model_timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model_name,
                        "stream": False,
                        "think": False,
                        "keep_alive": settings.model_keep_alive,
                        "format": "json",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "options": {
                            "temperature": 0.2,
                            "num_ctx": settings.model_context_size,
                            "num_predict": settings.model_max_output_tokens,
                        },
                    },
                )
                response.raise_for_status()
            payload = response.json()
            text = payload.get("message", {}).get("content", "")
            eval_count = payload.get("eval_count")
            eval_duration = payload.get("eval_duration")
            tokens_per_second = None
            if eval_count and eval_duration:
                tokens_per_second = eval_count / (eval_duration / 1_000_000_000)
            return ModelOpinion(
                model_name=model_name,
                success=True,
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=payload.get("prompt_eval_count"),
                response_tokens=eval_count,
                tokens_per_second=tokens_per_second,
                response_text=text,
                parsed=_extract_json(text),
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return ModelOpinion(
                model_name=model_name,
                success=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=None,
                response_tokens=None,
                tokens_per_second=None,
                response_text="",
                parsed=None,
                error=str(exc),
            )
