from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crawlers.base import SafeFetcher
from app.crawlers.official_specs import (
    SPEC_BOUNDS,
    completeness,
    extract_official_image,
    is_likely_product_image_url,
    parse_memory_variants,
    parse_official_specs,
    parse_release_date,
)
from app.models import Brand, CrawlLog, PhoneModel, PhoneVariant


@dataclass(frozen=True)
class CatalogSource:
    brand: str
    model_name: str
    official_url: str
    fallback_variants: str = ""
    source_note: str = ""


@dataclass
class CollectedPhone:
    source: CatalogSource
    final_url: str
    fetched_at: datetime
    evidence_path: str
    specs: dict[str, Any]
    release_date: date | None
    variants: list[dict[str, Any]]
    image: dict[str, str] | None
    completeness: float


def read_sources(path: Path) -> list[CatalogSource]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"brand", "model_name", "official_url"}
    if not rows or required - set(rows[0]):
        raise ValueError("官网目录清单缺少 brand、model_name 或 official_url")
    sources = [CatalogSource(**{field: (row.get(field) or "").strip() for field in CatalogSource.__dataclass_fields__}) for row in rows]
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.brand.casefold(), source.model_name.casefold())
        if key in seen:
            raise ValueError(f"官网目录存在重复机型：{source.brand} {source.model_name}")
        if not source.official_url.startswith("https://"):
            raise ValueError(f"官网来源必须使用 HTTPS：{source.official_url}")
        seen.add(key)
    return sources


def _page_matches_model(model_name: str, text: str) -> bool:
    compact_text = re.sub(r"[\s\-+]+", "", text).casefold()
    compact_model = re.sub(r"[\s\-+]+", "", model_name).casefold()
    candidates = {compact_model}
    for prefix in ("huawei", "oppo", "samsung", "vivo"):
        candidates.add(compact_model.removeprefix(prefix))
    return any(len(candidate) >= 4 and candidate in compact_text for candidate in candidates)


def _fallback_variants(value: str) -> list[dict[str, Any]]:
    variants = []
    for item in filter(None, (part.strip() for part in value.split(";"))):
        match = re.fullmatch(r"(?:(4|6|8|12|16|18|24|32)\s*\+\s*)?(64|128|256|512|1024|2048|1TB|2TB)", item, re.IGNORECASE)
        if not match:
            raise ValueError(f"官网回退内存版本格式无效：{item}")
        ram = int(match.group(1)) if match.group(1) else None
        raw_storage = match.group(2).upper()
        storage = int(raw_storage[:-2]) * 1024 if raw_storage.endswith("TB") else int(raw_storage)
        variants.append({
            "ram_gb": ram,
            "storage_gb": storage,
            "variant_name": (
                f"{ram}GB+{storage if storage < 1024 else str(storage // 1024) + 'TB'}"
                if ram is not None else f"{storage if storage < 1024 else str(storage // 1024) + 'TB'}"
            ),
            "launch_price": None,
        })
    return variants


def collect_source(source: CatalogSource, fetcher: SafeFetcher) -> CollectedPhone:
    result = fetcher.fetch(source.official_url)
    if not _page_matches_model(source.model_name, result.visible_text):
        raise ValueError("页面内容与机型名称不匹配，拒绝入库")
    specs = parse_official_specs(result.visible_text)
    variants = parse_memory_variants(result.visible_text) or _fallback_variants(source.fallback_variants)
    return CollectedPhone(
        source=source,
        final_url=result.url,
        fetched_at=result.fetched_at,
        evidence_path=str(result.text_path),
        specs=specs,
        release_date=parse_release_date(result.visible_text),
        variants=variants,
        image=extract_official_image(result.html, result.url, source.model_name),
        completeness=completeness(specs),
    )


def collect_catalog(
    sources: list[CatalogSource], *, minimum_interval: float = 1.5, limit: int | None = None
) -> tuple[list[CollectedPhone], list[dict[str, str]]]:
    fetcher = SafeFetcher(minimum_interval=minimum_interval)
    collected: list[CollectedPhone] = []
    failures: list[dict[str, str]] = []
    for source in sources[:limit]:
        try:
            collected.append(collect_source(source, fetcher))
        except Exception as exc:
            failures.append({"brand": source.brand, "model_name": source.model_name, "url": source.official_url, "error": str(exc)})
    return collected, failures


def _upsert_phone(db: Session, item: CollectedPhone) -> tuple[int, int, int]:
    brand = db.scalar(select(Brand).where(Brand.name == item.source.brand))
    brands_added = 0
    if brand is None:
        brand = Brand(name=item.source.brand, official_url=item.final_url, is_active=True)
        db.add(brand)
        db.flush()
        brands_added = 1
    phone = db.scalar(select(PhoneModel).where(
        PhoneModel.brand_id == brand.id,
        PhoneModel.model_name == item.source.model_name,
    ))
    phones_added = 0
    if phone is None:
        phone = PhoneModel(brand_id=brand.id, model_name=item.source.model_name)
        db.add(phone)
        db.flush()
        phones_added = 1
    fields = {
        "release_date": item.release_date,
        "cpu": item.specs.get("cpu"),
        "screen_size": item.specs.get("screen_size"),
        "resolution": item.specs.get("resolution"),
        "refresh_rate": int(item.specs["refresh_rate"]) if item.specs.get("refresh_rate") else None,
        "main_camera_mp": item.specs.get("main_camera_mp"),
        "battery_mah": int(item.specs["battery_mah"]) if item.specs.get("battery_mah") else None,
        "charging_w": item.specs.get("charging_w"),
        "weight_g": item.specs.get("weight_g"),
        "thickness_mm": item.specs.get("thickness_mm"),
        "operating_system": item.specs.get("operating_system"),
    }
    for field, value in fields.items():
        if value is not None:
            setattr(phone, field, value)
    for field, (minimum, maximum) in SPEC_BOUNDS.items():
        existing = getattr(phone, field)
        if existing is not None and not minimum <= float(existing) <= maximum:
            setattr(phone, field, None)
    if item.specs.get("main_camera_mp"):
        phone.camera_summary = f"官方规格标注 {item.specs['main_camera_mp']:g} MP 主摄"
    if item.image:
        phone.image_url = item.image["image_url"]
        phone.image_source_url = item.image["image_source_url"]
    elif phone.image_url and not is_likely_product_image_url(phone.image_url):
        phone.image_url = None
        phone.image_source_url = None
    phone.sale_status = "on_sale"
    phone.official_url = item.final_url
    phone.source_url = item.final_url
    phone.source_checked_at = item.fetched_at
    phone.data_quality = "official_verified" if item.completeness >= 66.7 and item.variants else "official_partial"
    phone.is_active = True

    variants_added = 0
    for parsed in item.variants:
        variant = db.scalar(select(PhoneVariant).where(
            PhoneVariant.model_id == phone.id,
            PhoneVariant.ram_gb == parsed["ram_gb"],
            PhoneVariant.storage_gb == parsed["storage_gb"],
        ))
        if variant is None:
            variant = PhoneVariant(
                model_id=phone.id,
                ram_gb=parsed["ram_gb"],
                storage_gb=parsed["storage_gb"],
                variant_name=str(parsed["variant_name"]),
                is_active=True,
            )
            db.add(variant)
            variants_added += 1
        if parsed.get("launch_price") is not None:
            variant.launch_price = parsed["launch_price"]
            variant.launch_price_source = item.final_url
    return brands_added, phones_added, variants_added


def import_collected(db: Session, collected: list[CollectedPhone]) -> dict[str, int]:
    counters = {"brands_added": 0, "phones_added": 0, "phones_updated": 0, "variants_added": 0}
    for item in collected:
        brand_count, phone_count, variant_count = _upsert_phone(db, item)
        counters["brands_added"] += brand_count
        counters["phones_added"] += phone_count
        counters["phones_updated"] += 0 if phone_count else 1
        counters["variants_added"] += variant_count
    db.commit()
    return counters


def write_catalog_report(
    path: Path,
    collected: list[CollectedPhone],
    failures: list[dict[str, str]],
    counters: dict[str, int],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"collected": len(collected), "failed": len(failures), **counters},
        "quality": {
            "with_release_date": sum(item.release_date is not None for item in collected),
            "with_variants": sum(bool(item.variants) for item in collected),
            "with_official_image": sum(bool(item.image) for item in collected),
            "average_spec_completeness": round(sum(item.completeness for item in collected) / len(collected), 1) if collected else 0,
        },
        "items": [
            {
                "brand": item.source.brand,
                "model_name": item.source.model_name,
                "url": item.final_url,
                "release_date": item.release_date.isoformat() if item.release_date else None,
                "variant_count": len(item.variants),
                "spec_completeness": item.completeness,
                "evidence_path": item.evidence_path,
            }
            for item in collected
        ],
        "failures": failures,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
