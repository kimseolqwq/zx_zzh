from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BASE_DIR, DATA_DIR
from app.models import Brand, PhoneModel, PhoneVariant, PlatformListing, PriceSnapshot


PLATFORM_DOMAINS = {
    "jd": ("jd.com",),
    "tmall": ("tmall.com", "taobao.com"),
    "pdd": ("yangkeduo.com", "pinduoduo.com"),
}


def _store_whitelist() -> set[tuple[str, str, str]]:
    path = BASE_DIR / "config" / "official_store_whitelist.csv"
    if not path.is_file():
        raise ValueError("缺少 config/official_store_whitelist.csv，不能核验官方店")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return {
            (
                (row.get("platform") or "").strip().lower(),
                (row.get("brand") or "").strip().casefold(),
                (row.get("store_name") or "").strip().casefold(),
            )
            for row in rows
            if (row.get("platform") or "").strip()
            and (row.get("brand") or "").strip()
            and (row.get("store_name") or "").strip()
            and (row.get("verified_at") or "").strip()
        }


def _text(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _money(row: dict[str, str], key: str) -> Decimal | None:
    value = _text(row, key)
    if not value:
        return None
    try:
        parsed = Decimal(value).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"{key} 价格格式错误：{value}") from exc
    if not 300 <= parsed <= 30000:
        raise ValueError(f"{key} 超出合理范围：{value}")
    return parsed


def _valid_product_url(platform: str, url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return url.startswith("https://") and any(host == domain or host.endswith(f".{domain}") for domain in PLATFORM_DOMAINS[platform])


def _validated_evidence_path(value: str, line_no: int) -> str:
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else BASE_DIR / candidate).resolve()
    allowed_root = (DATA_DIR / "raw").resolve()
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(f"第 {line_no} 行证据文件必须位于 data/raw 目录")
    if not resolved.is_file():
        raise ValueError(f"第 {line_no} 行证据文件不存在：{value}")
    return resolved.relative_to(BASE_DIR).as_posix()


def import_reviewed_prices(db: Session, path: Path, *, dry_run: bool = False) -> dict[str, int]:
    counters = {"rows": 0, "approved": 0, "skipped": 0, "duplicates_skipped": 0, "listings_added": 0, "snapshots_added": 0}
    allowed_stores = _store_whitelist()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"brand", "model_name", "storage_gb", "platform", "product_url", "review_status"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"价格审核 CSV 缺少列：{', '.join(sorted(missing))}")
        for line_no, row in enumerate(reader, start=2):
            counters["rows"] += 1
            if _text(row, "review_status").lower() != "approved":
                counters["skipped"] += 1
                continue
            platform = _text(row, "platform").lower()
            if platform not in PLATFORM_DOMAINS:
                raise ValueError(f"第 {line_no} 行平台无效：{platform}")
            product_url = _text(row, "product_url")
            if not _valid_product_url(platform, product_url):
                raise ValueError(f"第 {line_no} 行不是 {platform} 允许的商品域名")
            store_name = _text(row, "store_name")
            if not store_name or not any(label in store_name for label in ("官方旗舰店", "自营")):
                raise ValueError(f"第 {line_no} 行店铺名称未通过官方店规则")
            store_key = (platform, _text(row, "brand").casefold(), store_name.casefold())
            if store_key not in allowed_stores:
                raise ValueError(f"第 {line_no} 行店铺未进入官方店白名单：{store_name}")
            if not _text(row, "reviewer"):
                raise ValueError(f"第 {line_no} 行缺少审核人")
            evidence_text = _text(row, "evidence_text_path")
            screenshot = _text(row, "screenshot_path")
            if not evidence_text and not screenshot:
                raise ValueError(f"第 {line_no} 行缺少文本或截图证据")
            evidence_text = _validated_evidence_path(evidence_text, line_no) if evidence_text else ""
            screenshot = _validated_evidence_path(screenshot, line_no) if screenshot else ""

            brand = db.scalar(select(Brand).where(Brand.name == _text(row, "brand")))
            phone = db.scalar(select(PhoneModel).where(
                PhoneModel.brand_id == (brand.id if brand else -1),
                PhoneModel.model_name == _text(row, "model_name"),
            ))
            ram_text = _text(row, "ram_gb")
            storage = int(_text(row, "storage_gb"))
            ram = int(ram_text) if ram_text else None
            variant = db.scalar(select(PhoneVariant).where(
                PhoneVariant.model_id == (phone.id if phone else -1),
                PhoneVariant.ram_gb == ram,
                PhoneVariant.storage_gb == storage,
            ))
            if variant is None:
                raise ValueError(f"第 {line_no} 行找不到对应内存版本")

            external_id = _text(row, "external_id") or hashlib.sha256(product_url.encode()).hexdigest()[:20]
            sku_text = _text(row, "sku_text") or variant.variant_name
            listing = db.scalar(select(PlatformListing).where(
                PlatformListing.platform == platform,
                PlatformListing.external_id == external_id,
                PlatformListing.sku_text == sku_text,
            ))
            if listing is None:
                listing = PlatformListing(
                    variant_id=variant.id,
                    platform=platform,
                    store_name=store_name,
                    store_verified=True,
                    external_id=external_id,
                    sku_text=sku_text,
                    product_title=_text(row, "product_title") or f"{phone.model_name} {variant.variant_name}",
                    product_url=product_url,
                    region=_text(row, "region") or "中国大陆",
                    is_active=True,
                )
                db.add(listing)
                db.flush()
                counters["listings_added"] += 1
            else:
                listing.variant_id = variant.id
                listing.store_name = store_name
                listing.store_verified = True
                listing.product_url = product_url
                listing.is_active = True
            listing.last_checked_at = datetime.now(timezone.utc)
            prices = [_money(row, key) for key in ("regular_price", "public_sale_price")]
            if not any(price is not None for price in prices):
                raise ValueError(f"第 {line_no} 行没有任何价格")
            in_stock = _text(row, "in_stock").lower() not in {"false", "0", "no"}
            previous = db.scalar(
                select(PriceSnapshot)
                .where(PriceSnapshot.listing_id == listing.id)
                .order_by(PriceSnapshot.crawled_at.desc(), PriceSnapshot.id.desc())
                .limit(1)
            )
            is_duplicate = previous is not None and all((
                previous.regular_price == prices[0],
                previous.public_sale_price == prices[1],
                previous.in_stock == in_stock,
                previous.crawl_status == "reviewed",
                previous.evidence_text_path == (evidence_text or None),
                previous.screenshot_path == (screenshot or None),
            ))
            if is_duplicate:
                counters["duplicates_skipped"] += 1
            else:
                db.add(PriceSnapshot(
                    listing_id=listing.id,
                    regular_price=prices[0],
                    public_sale_price=prices[1],
                    promotion_labels="人工审核公开价格",
                    in_stock=in_stock,
                    crawl_status="reviewed",
                    evidence_text_path=evidence_text or None,
                    screenshot_path=screenshot or None,
                ))
                counters["snapshots_added"] += 1
            counters["approved"] += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return counters
