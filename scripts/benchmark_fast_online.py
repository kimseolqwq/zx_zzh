from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.config import settings
from app.services.ollama import OllamaClient
from app.services.recommendation import Requirements, recommend


CASES = (
    Requirements(5000, "摄影创作", None, 256, "旅行拍照，希望成像稳定"),
    Requirements(5000, "重度游戏", None, 256, "高帧游戏，重视性能和续航"),
    Requirements(5000, "轻薄续航", None, 256, "经常外出，希望续航长"),
    Requirements(5000, "综合体验", None, 256, "日常使用，兼顾性能和拍照"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="验证在线三模型推荐是否达到交互延迟目标")
    parser.add_argument("--limit-seconds", type=float, default=10.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "benchmark" / "fast-online-results.json",
    )
    args = parser.parse_args()
    warmup = OllamaClient().warm_models(settings.ollama_models)
    rows = []
    with SessionLocal() as db:
        for case in CASES:
            result = recommend(db, case)
            rows.append(
                {
                    "usage": case.usage,
                    "latency_seconds": round(result.get("total_latency_ms", 0) / 1000, 3),
                    "result_count": len(result.get("results", [])),
                    "top3": [f"{item['brand']} {item['model']} {item['variant']}" for item in result.get("results", [])],
                    "valid_model_votes": [item["model_votes"] for item in result.get("results", [])],
                    "within_limit": result.get("total_latency_ms", float("inf")) <= args.limit_seconds * 1000,
                }
            )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "limit_seconds": args.limit_seconds,
        "warmup": warmup,
        "all_within_limit": all(row["within_limit"] for row in rows),
        "all_have_three_results": all(row["result_count"] == 3 for row in rows),
        "all_use_three_models": all(
            row["valid_model_votes"] and all(votes == 3 for votes in row["valid_model_votes"])
            for row in rows
        ),
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["all_within_limit"] or not payload["all_have_three_results"] or not payload["all_use_three_models"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
