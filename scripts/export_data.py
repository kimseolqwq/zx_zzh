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
        phones = db.scalars(
            select(PhoneModel).options(selectinload(PhoneModel.brand)).order_by(PhoneModel.id)
        ).all()
        variants = db.scalars(
            select(PhoneVariant)
            .options(selectinload(PhoneVariant.model).selectinload(PhoneModel.brand))
            .order_by(PhoneVariant.id)
        ).all()
        listings = db.scalars(
            select(PlatformListing)
            .options(
                selectinload(PlatformListing.variant)
                .selectinload(PhoneVariant.model)
                .selectinload(PhoneModel.brand)
            )
            .order_by(PlatformListing.id)
        ).all()
        prices = db.scalars(select(PriceSnapshot).order_by(PriceSnapshot.id)).all()
        listing_by_id = {item.id: item for item in listings}
        write_csv(
            output / "phones.csv",
            [
                "id", "brand", "model_name", "release_date", "sale_status", "cpu",
                "screen_size", "screen_type", "resolution", "refresh_rate",
                "main_camera_mp", "camera_summary", "battery_mah", "charging_w",
                "wireless_charging_w", "weight_g", "thickness_mm", "waterproof",
                "operating_system", "image_url", "image_source_url", "official_url",
                "source_url", "source_checked_at", "data_quality", "is_active",
                "created_at", "updated_at",
            ],
            (
                (
                    p.id, p.brand.name, p.model_name, p.release_date, p.sale_status, p.cpu,
                    p.screen_size, p.screen_type, p.resolution, p.refresh_rate,
                    p.main_camera_mp, p.camera_summary, p.battery_mah, p.charging_w,
                    p.wireless_charging_w, p.weight_g, p.thickness_mm, p.waterproof,
                    p.operating_system, p.image_url, p.image_source_url, p.official_url,
                    p.source_url, p.source_checked_at, p.data_quality, p.is_active,
                    p.created_at, p.updated_at,
                )
                for p in phones
            ),
        )
        write_csv(
            output / "variants.csv",
            [
                "id", "model_id", "brand", "model_name", "ram_gb", "storage_gb",
                "variant_name", "launch_price", "launch_price_source", "color_limited",
                "is_active", "created_at", "updated_at",
            ],
            (
                (
                    v.id, v.model_id, v.model.brand.name, v.model.model_name, v.ram_gb,
                    v.storage_gb, v.variant_name, v.launch_price, v.launch_price_source,
                    v.color_limited, v.is_active, v.created_at, v.updated_at,
                )
                for v in variants
            ),
        )
        write_csv(
            output / "listings.csv",
            [
                "id", "variant_id", "brand", "model_name", "variant_name", "platform",
                "store_name", "store_verified", "external_id", "sku_text", "product_title",
                "product_url", "region", "is_active", "last_checked_at", "created_at", "updated_at",
            ],
            (
                (
                    item.id, item.variant_id, item.variant.model.brand.name,
                    item.variant.model.model_name, item.variant.variant_name, item.platform,
                    item.store_name, item.store_verified, item.external_id, item.sku_text,
                    item.product_title, item.product_url, item.region, item.is_active,
                    item.last_checked_at, item.created_at, item.updated_at,
                )
                for item in listings
            ),
        )
        write_csv(
            output / "price_snapshots.csv",
            [
                "id", "listing_id", "brand", "model_name", "variant_name", "platform",
                "store_name", "regular_price", "public_sale_price", "displayed_gov_price",
                "estimated_gov_price", "billion_subsidy_price", "promotion_labels",
                "promotion_stackable", "in_stock", "crawl_status", "evidence_text_path",
                "screenshot_path", "crawled_at",
            ],
            (
                (
                    price.id, price.listing_id,
                    listing_by_id[price.listing_id].variant.model.brand.name,
                    listing_by_id[price.listing_id].variant.model.model_name,
                    listing_by_id[price.listing_id].variant.variant_name,
                    listing_by_id[price.listing_id].platform,
                    listing_by_id[price.listing_id].store_name,
                    price.regular_price, price.public_sale_price, price.displayed_gov_price,
                    price.estimated_gov_price, price.billion_subsidy_price,
                    price.promotion_labels, price.promotion_stackable, price.in_stock,
                    price.crawl_status, price.evidence_text_path, price.screenshot_path,
                    price.crawled_at,
                )
                for price in prices
            ),
        )
        reviewed_prices = [
            price
            for price in prices
            if price.crawl_status == "reviewed"
            and listing_by_id[price.listing_id].is_active
            and listing_by_id[price.listing_id].store_verified
        ]
        write_csv(
            output / "reviewed_market_prices.csv",
            [
                "snapshot_id", "brand", "model_name", "variant_name", "platform",
                "store_name", "product_url", "regular_price", "public_sale_price",
                "displayed_gov_price", "billion_subsidy_price", "promotion_labels",
                "evidence_text_path", "screenshot_path", "crawled_at",
            ],
            (
                (
                    price.id, listing_by_id[price.listing_id].variant.model.brand.name,
                    listing_by_id[price.listing_id].variant.model.model_name,
                    listing_by_id[price.listing_id].variant.variant_name,
                    listing_by_id[price.listing_id].platform,
                    listing_by_id[price.listing_id].store_name,
                    listing_by_id[price.listing_id].product_url,
                    price.regular_price, price.public_sale_price,
                    price.displayed_gov_price, price.billion_subsidy_price,
                    price.promotion_labels, price.evidence_text_path,
                    price.screenshot_path, price.crawled_at,
                )
                for price in reviewed_prices
            ),
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
