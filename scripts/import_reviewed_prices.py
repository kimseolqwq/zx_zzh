from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.market_import import import_reviewed_prices
from app.database import SessionLocal, checkpoint_database


def main() -> None:
    parser = argparse.ArgumentParser(description="将已审核并留证的三平台价格写入 SQLite")
    parser.add_argument("file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        result = import_reviewed_prices(db, args.file, dry_run=args.dry_run)
    if not args.dry_run:
        checkpoint_database()
    print(("校验通过" if args.dry_run else "入库完成"), result)


if __name__ == "__main__":
    main()
