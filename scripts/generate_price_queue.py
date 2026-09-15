from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import DATA_DIR
from app.database import SessionLocal
from app.models import PhoneModel, PhoneVariant, PlatformListing


HEADERS = [
    "priority", "release_date", "launch_price",
    "brand", "model_name", "ram_gb", "storage_gb", "variant_name", "platform", "search_url",
    "store_name", "external_id", "sku_text", "product_title", "product_url", "region",
    "regular_price", "public_sale_price", "gov_price", "billion_subsidy_price",
    "promotion_labels", "promotion_stackable", "in_stock", "evidence_text_path", "screenshot_path",
    "reviewer", "review_status",
]


def _existing_rows(path: Path) -> dict[tuple[str, str, str, str, str], dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        (row.get("brand", ""), row.get("model_name", ""), row.get("ram_gb", ""), row.get("storage_gb", ""), row.get("platform", "")): row
        for row in rows
    }


def _latest_reviewed_listing(variant: PhoneVariant, platform: str):
    listings = [item for item in variant.listings if item.platform == platform and item.is_active and item.store_verified]
    if not listings:
        return None, None
    listing = max(listings, key=lambda item: item.last_checked_at or item.created_at)
    reviewed = [snapshot for snapshot in listing.prices if snapshot.crawl_status == "reviewed"]
    snapshot = max(reviewed, key=lambda item: (item.crawled_at, item.id)) if reviewed else None
    return listing, snapshot


def _release_sort_value(value: str) -> int:
    return -date.fromisoformat(value).toordinal() if value else 0


def generate(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_rows(path)
    recent_cutoff = date.today() - timedelta(days=366)
    with SessionLocal() as db:
        phones = db.scalars(select(PhoneModel).options(
            selectinload(PhoneModel.brand),
            selectinload(PhoneModel.variants).selectinload(PhoneVariant.listings).selectinload(PlatformListing.prices),
        ).where(PhoneModel.is_active.is_(True)).order_by(PhoneModel.release_date.desc(), PhoneModel.id.desc())).unique().all()
        rows = []
        for phone in phones:
            for variant in phone.variants:
                if not variant.is_active:
                    continue
                query = quote(f"{phone.brand.name} {phone.model_name} {variant.variant_name} 官方旗舰店")
                urls = {
                    "jd": f"https://search.jd.com/Search?keyword={query}",
                    "tmall": f"https://list.tmall.com/search_product.htm?q={query}",
                    "pdd": f"https://mobile.yangkeduo.com/search_result.html?search_key={query}",
                }
                for platform, search_url in urls.items():
                    key = (phone.brand.name, phone.model_name, str(variant.ram_gb or ""), str(variant.storage_gb), platform)
                    row = {header: "" for header in HEADERS}
                    row.update(existing.get(key, {}))
                    listing, snapshot = _latest_reviewed_listing(variant, platform)
                    row.update({
                        "priority": "HIGH" if listing is None else "MEDIUM",
                        "release_date": phone.release_date.isoformat() if phone.release_date else "",
                        "launch_price": str(variant.launch_price or ""),
                        "brand": phone.brand.name,
                        "model_name": phone.model_name,
                        "ram_gb": str(variant.ram_gb or ""),
                        "storage_gb": str(variant.storage_gb),
                        "variant_name": variant.variant_name,
                        "platform": platform,
                        "search_url": search_url,
                        "sku_text": row.get("sku_text") or variant.variant_name,
                        "region": row.get("region") or "中国大陆",
                        "promotion_stackable": row.get("promotion_stackable") or "unknown",
                        "in_stock": row.get("in_stock") or "true",
                        "review_status": row.get("review_status") or "pending",
                    })
                    if phone.release_date and phone.release_date < recent_cutoff and listing is None:
                        row["priority"] = "MEDIUM"
                    if listing and snapshot:
                        row.update({
                            "store_name": listing.store_name,
                            "external_id": listing.external_id,
                            "sku_text": listing.sku_text,
                            "product_title": listing.product_title or "",
                            "product_url": listing.product_url,
                            "region": listing.region,
                            "regular_price": str(snapshot.regular_price or ""),
                            "public_sale_price": str(snapshot.public_sale_price or ""),
                            "gov_price": str(snapshot.displayed_gov_price or ""),
                            "billion_subsidy_price": str(snapshot.billion_subsidy_price or ""),
                            "promotion_labels": snapshot.promotion_labels or "",
                            "promotion_stackable": snapshot.promotion_stackable,
                            "in_stock": str(snapshot.in_stock).lower(),
                            "evidence_text_path": snapshot.evidence_text_path or "",
                            "screenshot_path": snapshot.screenshot_path or "",
                            "review_status": "approved",
                        })
                    rows.append(row)
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    rows.sort(key=lambda row: (
        priority_order.get(row["priority"], 9),
        _release_sort_value(row["release_date"]),
        row["brand"], row["model_name"], int(row["storage_gb"]), row["platform"],
    ))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="为每个内存版本生成京东/天猫/拼多多人工审核采集队列")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "review" / "price_review_queue.csv")
    args = parser.parse_args()
    count = generate(args.output)
    print(f"已生成 {count} 条待审核任务：{args.output.resolve()}")


if __name__ == "__main__":
    main()
