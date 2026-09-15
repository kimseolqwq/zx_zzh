from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.services.ollama import OllamaClient


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    results = OllamaClient().warm_models(settings.ollama_models)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not results or not all(item["success"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
