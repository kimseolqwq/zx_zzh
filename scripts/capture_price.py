from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.browser_prices import capture


def main() -> None:
    parser = argparse.ArgumentParser(description="浏览器低频采集公开商品页并保留截图证据")
    parser.add_argument("url")
    parser.add_argument("item_id")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    print(json.dumps(capture(args.url, args.item_id, headless=args.headless), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
