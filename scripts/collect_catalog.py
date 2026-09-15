from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATA_DIR
from app.crawlers.catalog_pipeline import collect_catalog, import_collected, read_sources, write_catalog_report
from app.database import SessionLocal, checkpoint_database, create_schema, engine


def main() -> None:
    parser = argparse.ArgumentParser(description="低频采集品牌官网参数并幂等写入 SQLite")
    parser.add_argument("--sources", type=Path, default=PROJECT_ROOT / "config" / "official_catalog_sources.csv")
    parser.add_argument("--interval", type=float, default=1.5, help="同一站点请求最小间隔秒数")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--brand",
        action="append",
        help="只采集指定品牌，可重复传入；品牌名不区分英文大小写",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    create_schema()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    database_path = Path(engine.url.database or "")
    backup_path = DATA_DIR / "backups" / f"before-catalog-{stamp}.db"
    if database_path.exists() and not args.dry_run:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(database_path, backup_path)

    sources = read_sources(args.sources)
    if args.brand:
        selected_brands = {item.strip().casefold() for item in args.brand if item.strip()}
        sources = [item for item in sources if item.brand.casefold() in selected_brands]
        if not sources:
            parser.error("--brand 未匹配官网目录中的任何品牌")
    collected, failures = collect_catalog(sources, minimum_interval=args.interval, limit=args.limit)
    counters = {"brands_added": 0, "phones_added": 0, "phones_updated": 0, "variants_added": 0}
    if not args.dry_run:
        with SessionLocal() as db:
            counters = import_collected(db, collected)
        checkpoint_database()
    report_path = DATA_DIR / "reports" / f"catalog-{stamp}.json"
    write_catalog_report(report_path, collected, failures, counters)
    print({"sources": len(sources[:args.limit]), "collected": len(collected), "failed": len(failures), **counters})
    print(f"质量报告：{report_path.resolve()}")
    if database_path.exists() and not args.dry_run:
        print(f"入库前备份：{backup_path.resolve()}")


if __name__ == "__main__":
    main()
