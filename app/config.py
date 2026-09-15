from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(BASE_DIR / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "择机")
    secret_key: str = os.getenv("APP_SECRET_KEY", secrets.token_urlsafe(32))
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "Admin@123456")
    session_https_only: bool = _as_bool(os.getenv("SESSION_HTTPS_ONLY"), False)
    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{(DATA_DIR / 'phone_recommender.db').as_posix()}"
    )
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_models: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv(
            "OLLAMA_MODELS", "qwen3:1.7b,qwen2.5:1.5b,gemma3:1b"
        ).split(",")
        if item.strip()
    )
    model_timeout_seconds: float = float(os.getenv("MODEL_TIMEOUT_SECONDS", "8.5"))
    model_context_size: int = int(os.getenv("MODEL_CONTEXT_SIZE", "2048"))
    model_max_output_tokens: int = int(os.getenv("MODEL_MAX_OUTPUT_TOKENS", "320"))
    model_keep_alive: str = os.getenv("MODEL_KEEP_ALIVE", "30m")
    target_region: str = os.getenv("TARGET_REGION", "中国大陆")


settings = Settings()
