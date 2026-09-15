from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import engine


def create_distribution_database(output: Path, *, include_reviewed_prices: bool = False) -> None:
    source_path = Path(engine.url.database or "")
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with sqlite3.connect(source_path) as source, sqlite3.connect(output) as target:
        source.backup(target)
        target.execute("PRAGMA foreign_keys=ON")
        sensitive_tables = (
            "model_runs", "recommendation_runs", "audit_logs", "users", "crawl_logs",
        )
        for table in sensitive_tables:
            target.execute(f'DELETE FROM "{table}"')
        if include_reviewed_prices:
            target.execute("DELETE FROM price_snapshots WHERE crawl_status != 'reviewed'")
            target.execute(
                "DELETE FROM platform_listings "
                "WHERE store_verified != 1 OR is_active != 1 "
                "OR id NOT IN (SELECT DISTINCT listing_id FROM price_snapshots)"
            )
        else:
            target.execute('DELETE FROM "price_snapshots"')
            target.execute('DELETE FROM "platform_listings"')
        has_sequence = target.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
        if has_sequence:
            cleared = list(sensitive_tables)
            if not include_reviewed_prices:
                cleared.extend(("price_snapshots", "platform_listings"))
            placeholders = ",".join("?" for _ in cleared)
            target.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                cleared,
            )
        target.commit()
        target.execute("VACUUM")
        target.execute("PRAGMA optimize")


def main() -> None:
    parser = argparse.ArgumentParser(description="创建不含账号、查询记录和未审核价格的课程分发数据库")
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--include-reviewed-prices",
        action="store_true",
        help="保留已人工审核、店铺已验证且仍有效的平台价格",
    )
    args = parser.parse_args()
    create_distribution_database(
        args.output,
        include_reviewed_prices=args.include_reviewed_prices,
    )
    print(f"分发数据库：{args.output.resolve()}")


if __name__ == "__main__":
    main()
