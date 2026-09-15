from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import DATA_DIR
from app.database import SessionLocal
from app.models import PhoneModel, PhoneVariant


HEADERS = [
    "brand", "model_name", "ram_gb", "storage_gb", "variant_name", "platform", "search_url",
    "store_name", "external_id", "sku_text", "product_title", "product_url", "region",
    "regular_price", "public_sale_price", "gov_price", "billion_subsidy_price",
    "promotion_labels", "promotion_stackable", "in_stock", "evidence_text_path", "screenshot_path",
    "reviewer", "review_status",
]


def generate(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        phones = db.scalars(select(PhoneModel).options(
            selectinload(PhoneModel.brand), selectinload(PhoneModel.variants)
        ).where(PhoneModel.is_active.is_(True))).unique().all()
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
                    rows.append([
                        phone.brand.name, phone.model_name, variant.ram_gb or "", variant.storage_gb,
                        variant.variant_name, platform, search_url, "", "", variant.variant_name, "", "", "中国大陆",
                        "", "", "", "", "", "unknown", "true", "", "", "", "pending",
                    ])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
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
