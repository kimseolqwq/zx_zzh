from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import DATA_DIR
from app.crawlers.official_specs import SPEC_BOUNDS
from app.database import SessionLocal, engine
from app.models import Brand, PhoneModel, PhoneVariant, PlatformListing, PriceSnapshot
from app.services.evaluation import is_meaningful


FIELDS = ("cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "weight_g", "image_url", "source_url")


def audit() -> dict:
    cutoff = date.today() - timedelta(days=730)
    with SessionLocal() as db:
        phones = db.scalars(select(PhoneModel).options(
            selectinload(PhoneModel.brand),
            selectinload(PhoneModel.variants).selectinload(PhoneVariant.listings).selectinload(PlatformListing.prices),
        ).where(PhoneModel.is_active.is_(True))).unique().all()
        variants = [variant for phone in phones for variant in phone.variants if variant.is_active]
        listings = [listing for variant in variants for listing in variant.listings if listing.is_active]
        snapshots = [snapshot for listing in listings for snapshot in listing.prices]
        reviewed_snapshots = [snapshot for snapshot in snapshots if snapshot.crawl_status == "reviewed"]
        reviewed_listing_ids = {snapshot.listing_id for snapshot in reviewed_snapshots}
        reviewed_listings = [listing for listing in listings if listing.id in reviewed_listing_ids and listing.store_verified]
        priced_variant_ids = {listing.variant_id for listing in reviewed_listings}
        platforms_by_variant: dict[int, set[str]] = {}
        for listing in reviewed_listings:
            platforms_by_variant.setdefault(listing.variant_id, set()).add(listing.platform)
        fresh_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        fresh_snapshots = [
            snapshot for snapshot in reviewed_snapshots
            if snapshot.crawled_at.replace(tzinfo=timezone.utc) >= fresh_cutoff
            if snapshot.crawled_at.tzinfo is None
        ] + [
            snapshot for snapshot in reviewed_snapshots
            if snapshot.crawled_at.tzinfo is not None and snapshot.crawled_at >= fresh_cutoff
        ]
        brand_counts = Counter(phone.brand.name for phone in phones)
        quality_counts = Counter(phone.data_quality for phone in phones)
        coverage = {
            field: round(sum(is_meaningful(getattr(phone, field)) for phone in phones) / len(phones) * 100, 1) if phones else 0
            for field in FIELDS
        }
        issues = []
        for phone in phones:
            missing = [field for field in FIELDS if not is_meaningful(getattr(phone, field))]
            if missing:
                issues.append({"severity": "medium", "type": "missing_specs", "phone": f"{phone.brand.name} {phone.model_name}", "detail": ", ".join(missing)})
            if phone.release_date is None:
                issues.append({"severity": "medium", "type": "release_date_unverified", "phone": f"{phone.brand.name} {phone.model_name}", "detail": "官网页未明确标注发布日期"})
            elif phone.release_date < cutoff:
                issues.append({"severity": "high", "type": "outside_two_year_window", "phone": f"{phone.brand.name} {phone.model_name}", "detail": phone.release_date.isoformat()})
            if not any(variant.is_active for variant in phone.variants):
                issues.append({"severity": "high", "type": "missing_variants", "phone": f"{phone.brand.name} {phone.model_name}", "detail": "未提取到明确内存组合"})
            invalid_fields = [
                field for field, (minimum, maximum) in SPEC_BOUNDS.items()
                if getattr(phone, field) is not None and not minimum <= float(getattr(phone, field)) <= maximum
            ]
            if invalid_fields:
                issues.append({"severity": "high", "type": "invalid_spec_range", "phone": f"{phone.brand.name} {phone.model_name}", "detail": ", ".join(invalid_fields)})
        invalid_active_links = [listing.id for listing in listings if "example.com" in listing.product_url or not listing.store_verified]
        if invalid_active_links:
            issues.append({"severity": "high", "type": "invalid_active_listing", "detail": invalid_active_links})
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": str(engine.url.database),
            "summary": {
                "brands": len(brand_counts), "phones": len(phones), "variants": len(variants),
                "active_verified_listings": len(reviewed_listings),
                "active_price_snapshots": len(reviewed_snapshots),
                "priced_variants": len(priced_variant_ids),
                "three_platform_variants": sum(platforms == {"jd", "tmall", "pdd"} for platforms in platforms_by_variant.values()),
                "fresh_price_snapshots_7d": len(fresh_snapshots),
                "price_evidence_snapshots": sum(bool(snapshot.evidence_text_path or snapshot.screenshot_path) for snapshot in reviewed_snapshots),
                "official_source_phones": sum(phone.data_quality.startswith("official_") or phone.data_quality == "manual_official_review" for phone in phones),
            },
            "reviewed_platform_counts": dict(sorted(Counter(listing.platform for listing in reviewed_listings).items())),
            "brand_counts": dict(sorted(brand_counts.items())),
            "quality_counts": dict(sorted(quality_counts.items())),
            "field_coverage_percent": coverage,
            "issue_counts": dict(Counter(issue["type"] for issue in issues)),
            "issues": issues,
        }


def write_reports(payload: dict) -> tuple[Path, Path]:
    report_dir = DATA_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "data-quality-latest.json"
    md_path = report_dir / "data-quality-latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = payload["summary"]
    lines = [
        "# 手机数据质量报告", "", f"生成时间：{payload['generated_at']}", "",
        "## 数据规模", "",
        f"- 品牌：{summary['brands']}", f"- 在售机型：{summary['phones']}", f"- 内存版本：{summary['variants']}",
        f"- 已核验平台商品：{summary['active_verified_listings']}", f"- 有效价格快照：{summary['active_price_snapshots']}",
        f"- 至少有一个平台价格的版本：{summary['priced_variants']}",
        f"- 三平台价格齐全的版本：{summary['three_platform_variants']}",
        f"- 7 天内价格快照：{summary['fresh_price_snapshots_7d']}",
        f"- 带证据的价格快照：{summary['price_evidence_snapshots']}",
        "", "## 已审核价格平台分布", "",
        *[f"- {platform}：{count} 条" for platform, count in payload["reviewed_platform_counts"].items()],
        "", "## 品牌分布", "",
        *[f"- {brand}：{count} 款" for brand, count in payload["brand_counts"].items()],
        "", "## 参数字段覆盖率", "",
        *[f"- {field}：{value}%" for field, value in payload["field_coverage_percent"].items()],
        "", "## 待处理问题", "",
        *[f"- {name}：{count}" for name, count in payload["issue_counts"].items()],
        "", "> 缺失值保持为空，不使用猜测值填充。电商价格只有在商品域名、官方店名称、审核人和留证文件同时通过时才允许入库。", "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> None:
    payload = audit()
    paths = write_reports(payload)
    print(payload["summary"])
    print(payload["field_coverage_percent"])
    print(f"报告：{paths[0].resolve()}\n{paths[1].resolve()}")


if __name__ == "__main__":
    main()
