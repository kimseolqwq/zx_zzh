from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.database import SessionLocal, checkpoint_database, create_schema
from app.models import Brand, PhoneModel, PhoneVariant, PlatformListing, PriceSnapshot


def text(row: dict[str, str], key: str) -> str | None:
    value = (row.get(key) or "").strip()
    return value or None


def integer(row: dict[str, str], key: str) -> int | None:
    value = text(row, key)
    return int(value) if value else None


def number(row: dict[str, str], key: str) -> float | None:
    value = text(row, key)
    return float(value) if value else None


def money(row: dict[str, str], key: str) -> Decimal | None:
    value = text(row, key)
    if not value:
        return None
    try:
        return Decimal(value).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"{key} 不是有效价格：{value}") from exc


def import_file(path: Path, dry_run: bool = False) -> dict[str, int]:
    counters = {"rows": 0, "brands": 0, "phones": 0, "variants": 0, "listings": 0, "prices": 0}
    create_schema()
    with path.open("r", encoding="utf-8-sig", newline="") as handle, SessionLocal() as db:
        reader = csv.DictReader(handle)
        required = {"brand", "model_name", "storage_gb"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV 缺少必填列：{', '.join(sorted(missing))}")
        try:
            for line_no, row in enumerate(reader, start=2):
                if not any((value or "").strip() for value in row.values()):
                    continue
                counters["rows"] += 1
                brand_name = text(row, "brand")
                model_name = text(row, "model_name")
                if not brand_name or not model_name:
                    raise ValueError(f"第 {line_no} 行缺少品牌或型号")

                brand = db.scalar(select(Brand).where(Brand.name == brand_name))
                if not brand:
                    brand = Brand(name=brand_name, official_url=text(row, "brand_official_url"), is_active=True)
                    db.add(brand); db.flush(); counters["brands"] += 1

                phone = db.scalar(select(PhoneModel).where(PhoneModel.brand_id == brand.id, PhoneModel.model_name == model_name))
                if not phone:
                    released = text(row, "release_date")
                    phone = PhoneModel(
                        brand_id=brand.id, model_name=model_name,
                        release_date=date.fromisoformat(released) if released else None,
                        sale_status=text(row, "sale_status") or "on_sale", cpu=text(row, "cpu"),
                        screen_size=number(row, "screen_size"), refresh_rate=integer(row, "refresh_rate"),
                        main_camera_mp=number(row, "main_camera_mp"), camera_summary=text(row, "camera_summary"),
                        battery_mah=integer(row, "battery_mah"), charging_w=number(row, "charging_w"),
                        weight_g=number(row, "weight_g"), official_url=text(row, "official_url"),
                        image_url=text(row, "image_url"), image_source_url=text(row, "image_source_url"),
                        source_url=text(row, "source_url") or text(row, "official_url"),
                        source_checked_at=datetime.now(timezone.utc), data_quality=text(row, "data_quality") or "imported",
                        is_active=True,
                    )
                    db.add(phone); db.flush(); counters["phones"] += 1

                ram, storage = integer(row, "ram_gb"), integer(row, "storage_gb")
                if storage is None:
                    if (text(row, "data_quality") or "") == "official_catalog_only":
                        # A current official-catalog entry may be recorded before
                        # its dynamic specs page can be reviewed. It remains out
                        # of recommendation queries because it has no variant.
                        continue
                    raise ValueError(f"第 {line_no} 行 storage_gb 不能为空")
                variant = db.scalar(select(PhoneVariant).where(
                    PhoneVariant.model_id == phone.id, PhoneVariant.ram_gb == ram, PhoneVariant.storage_gb == storage
                ))
                if not variant:
                    variant = PhoneVariant(
                        model_id=phone.id, ram_gb=ram, storage_gb=storage,
                        variant_name=f"{ram}GB+{storage}GB" if ram else f"{storage}GB",
                        launch_price=money(row, "launch_price"), launch_price_source=text(row, "launch_price_source"),
                        is_active=True,
                    )
                    db.add(variant); db.flush(); counters["variants"] += 1

                platform, external_id, sku_text = text(row, "platform"), text(row, "external_id"), text(row, "sku_text")
                listing = None
                if platform and external_id:
                    sku_text = sku_text or variant.variant_name
                    listing = db.scalar(select(PlatformListing).where(
                        PlatformListing.platform == platform, PlatformListing.external_id == external_id,
                        PlatformListing.sku_text == sku_text,
                    ))
                    if not listing:
                        listing = PlatformListing(
                            variant_id=variant.id, platform=platform, store_name=text(row, "store_name") or "待核验官方旗舰店",
                            store_verified=(text(row, "store_verified") or "false").lower() in {"1", "true", "yes"},
                            external_id=external_id, sku_text=sku_text, product_title=text(row, "product_title"),
                            product_url=text(row, "product_url") or "https://example.invalid/pending",
                            region=text(row, "region") or "中国大陆", is_active=True,
                        )
                        db.add(listing); db.flush(); counters["listings"] += 1

                price_values = [money(row, key) for key in ("regular_price", "public_sale_price")]
                if listing and any(value is not None for value in price_values):
                    db.add(PriceSnapshot(
                        listing_id=listing.id, regular_price=price_values[0], public_sale_price=price_values[1],
                        promotion_labels="CSV导入",
                        in_stock=(text(row, "in_stock") or "true").lower() in {"1", "true", "yes"},
                        crawl_status="imported",
                    ))
                    counters["prices"] += 1
            if dry_run:
                db.rollback()
            else:
                db.commit()
        except Exception:
            db.rollback()
            raise
    return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="事务式批量导入手机目录；重复运行会跳过既有记录")
    parser.add_argument("file", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只校验，不写入数据库")
    args = parser.parse_args()
    result = import_file(args.file, args.dry_run)
    if not args.dry_run:
        checkpoint_database()
    mode = "校验通过（未写入）" if args.dry_run else "导入完成"
    print(mode, result)


if __name__ == "__main__":
    main()
