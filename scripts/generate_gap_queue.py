from __future__ import annotations

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import PhoneModel, PhoneVariant, PlatformListing
from app.services.evaluation import is_meaningful

FIELDS = ("release_date", "cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "weight_g", "image_url")
PLATFORMS = ("jd", "tmall", "pdd")


def main() -> None:
    output_dir = PROJECT_ROOT / "outputs"
    output_dir.mkdir(exist_ok=True)
    spec_rows, price_rows = [], []
    with SessionLocal() as db:
        phones = db.scalars(select(PhoneModel).options(
            selectinload(PhoneModel.brand),
            selectinload(PhoneModel.variants).selectinload(PhoneVariant.listings),
        ).where(PhoneModel.is_active.is_(True))).unique().all()
        for phone in phones:
            missing = [field for field in FIELDS if not is_meaningful(getattr(phone, field))]
            if missing or not phone.variants:
                spec_rows.append({
                    "priority": "HIGH" if not phone.variants else "MEDIUM",
                    "brand": phone.brand.name, "model": phone.model_name,
                    "missing_fields": ";".join(missing), "missing_variants": "yes" if not phone.variants else "no",
                    "official_url": phone.official_url or phone.source_url or "", "review_status": "pending", "review_note": "",
                })
            for variant in (item for item in phone.variants if item.is_active):
                existing = {item.platform for item in variant.listings if item.is_active and item.store_verified}
                for platform in PLATFORMS:
                    if platform not in existing:
                        price_rows.append({
                            "priority": "HIGH" if variant.launch_price is not None else "MEDIUM",
                            "brand": phone.brand.name, "model": phone.model_name, "variant": variant.variant_name,
                            "platform": platform, "launch_price": variant.launch_price or "", "official_store": "",
                            "product_url": "", "public_price": "", "gov_price": "", "billion_subsidy_price": "",
                            "evidence_path": "", "review_status": "pending", "reviewer": "",
                        })
    for name, rows in (("official-data-gap-queue-v1.7.csv", spec_rows), ("market-price-gap-queue-v1.7.csv", price_rows)):
        path = output_dir / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
        print(f"{name}: {len(rows)} rows -> {path.resolve()}")


if __name__ == "__main__":
    main()
