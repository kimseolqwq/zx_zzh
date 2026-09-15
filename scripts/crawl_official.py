from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.base import SafeFetcher
from app.crawlers.official_specs import completeness, extract_official_image, parse_official_specs


def main() -> None:
    parser = argparse.ArgumentParser(description="采集一个品牌官网规格页并输出结构化结果")
    parser.add_argument("url")
    parser.add_argument("--ignore-robots", action="store_true", help="仅在已人工确认允许时使用")
    args = parser.parse_args()
    result = SafeFetcher().fetch(args.url, respect_robots=not args.ignore_robots)
    specs = parse_official_specs(result.visible_text)
    print({"url": result.url, "completeness": completeness(specs), "specs": specs, "official_image": extract_official_image(result.html, result.url), "evidence": str(result.text_path)})


if __name__ == "__main__":
    main()
