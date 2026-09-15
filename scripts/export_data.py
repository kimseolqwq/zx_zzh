from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import BASE_DIR
from app.database import SessionLocal, engine
from app.models import Brand, PhoneModel, PhoneVariant, PlatformListing, PriceSnapshot


def write_csv(path: Path, headers: list[str], rows) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def export_all(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        phones = db.scalars(select(PhoneModel).options(selectinload(PhoneModel.brand))).all()
        variants = db.scalars(select(PhoneVariant).options(selectinload(PhoneVariant.model))).all()
        listings = db.scalars(select(PlatformListing).options(selectinload(PlatformListing.variant))).all()
        prices = db.scalars(select(PriceSnapshot)).all()
        write_csv(output / "phones.csv", ["id", "brand", "model_name", "release_date", "sale_status", "cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "image_url", "image_source_url", "official_url", "data_quality"],
                  ((p.id, p.brand.name, p.model_name, p.release_date, p.sale_status, p.cpu, p.screen_size, p.refresh_rate, p.main_camera_mp, p.battery_mah, p.image_url, p.image_source_url, p.official_url, p.data_quality) for p in phones))
        write_csv(output / "variants.csv", ["id", "model_id", "model_name", "ram_gb", "storage_gb", "variant_name", "launch_price", "is_active"],
                  ((v.id, v.model_id, v.model.model_name, v.ram_gb, v.storage_gb, v.variant_name, v.launch_price, v.is_active) for v in variants))
        write_csv(output / "listings.csv", ["id", "variant_id", "platform", "store_name", "store_verified", "external_id", "sku_text", "product_url", "is_active"],
                  ((x.id, x.variant_id, x.platform, x.store_name, x.store_verified, x.external_id, x.sku_text, x.product_url, x.is_active) for x in listings))
        write_csv(
            output / "price_snapshots.csv",
            ["id", "listing_id", "regular_price", "public_sale_price", "displayed_gov_price", "estimated_gov_price", "billion_subsidy_price", "promotion_labels", "promotion_stackable", "in_stock", "crawl_status", "evidence_text_path", "screenshot_path", "crawled_at"],
            ((p.id, p.listing_id, p.regular_price, p.public_sale_price, p.displayed_gov_price, p.estimated_gov_price, p.billion_subsidy_price, p.promotion_labels, p.promotion_stackable, p.in_stock, p.crawl_status, p.evidence_text_path, p.screenshot_path, p.crawled_at) for p in prices),
        )
    database_path = Path(engine.url.database or "")
    if database_path.exists():
        with sqlite3.connect(database_path) as source, sqlite3.connect(output / "phone_recommender.backup.db") as target:
            source.backup(target)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出核心数据表并创建 SQLite 备份")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "exports" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    export_all(args.output)
    print(f"已导出到：{args.output.resolve()}")


if __name__ == "__main__":
    main()
