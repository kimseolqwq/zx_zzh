from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import DATA_DIR
from app.crawlers.official_specs import completeness, parse_memory_variants, parse_official_specs, parse_release_date
from app.database import SessionLocal, checkpoint_database, engine
from app.models import Brand, PhoneModel, PhoneVariant


@dataclass(frozen=True)
class LocalSource:
    brand: str
    path: Path
    name_key: str
    url_keys: tuple[str, ...]
    variant_keys: tuple[str, ...] = ()


SOURCES = (
    LocalSource("小米", PROJECT_ROOT / "xiaomi_data" / "xiaomi_phones.json", "name", ("url",), ("original_name",)),
    LocalSource("vivo", PROJECT_ROOT / "vivo_data" / "vivo_phones_full.json", "name", ("url",), ("sku_name",)),
    LocalSource("OPPO", PROJECT_ROOT / "oppo_data" / "oppo_phones_temp.json", "name", ("specs_url", "url")),
    LocalSource("荣耀", PROJECT_ROOT / "rongyao_data" / "honor_phones.json", "name", ("spec_url", "url")),
    LocalSource("华为", PROJECT_ROOT / "huawei_phones_full.json", "name", ("url",)),
)

UPDATABLE_FIELDS = (
    "cpu", "screen_size", "screen_type", "refresh_rate", "main_camera_mp", "battery_mah",
    "charging_w", "wireless_charging_w", "wireless_charging_supported",
    "weight_g", "thickness_mm", "resolution", "waterproof", "waterproof_supported",
    "nfc", "five_g", "screen_shape", "telephoto", "operating_system",
)
VARIANT_SUFFIX_TOKENS = {
    "pro", "promax", "proplus", "ultra", "max", "mini", "plus", "turbo",
    "e", "s", "t", "x", "5g", "4g", "至尊版", "元气版", "活力版",
    "卫星通信版", "徕卡版", "典藏版", "非凡大师", "max版",
}


def normalise_name(value: str | None) -> str:
    text = (value or "").casefold()
    text = re.sub(r"pro\s*\+", "proplus", text)
    text = text.replace("+", "plus")
    for token in ("huawei", "honor", "荣耀", "华为", "xiaomi", "redmi", "oppo", "vivo", "一加", "oneplus"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def path_key(value: str | None) -> str:
    if not value:
        return ""
    parts = [part.casefold() for part in urlparse(value).path.split("/") if part]
    while parts and parts[-1] in {"spec", "specs", "index.html"}:
        parts.pop()
    return parts[-1] if parts else ""


def base_variant_text(value: str) -> str:
    return re.split(r"\s+\d{1,2}\s*GB\s*\+", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()


def candidate_names(source: LocalSource, row: dict[str, Any]) -> list[str]:
    names: list[str] = []
    primary = row.get(source.name_key)
    if isinstance(primary, str) and primary.strip():
        names.append(primary.strip())
    for key in source.variant_keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            name = base_variant_text(value)
            names.append(name)
            tokens = name.split()
            if source.brand == "vivo" and len(tokens) > 3:
                names.append(" ".join(tokens[:-1]))
    return list(dict.fromkeys(names))


def candidate_urls(source: LocalSource, row: dict[str, Any]) -> list[str]:
    return [value for key in source.url_keys if isinstance((value := row.get(key)), str) and value]


def match_record(phone: PhoneModel, source: LocalSource, rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    phone_name = normalise_name(phone.model_name)
    phone_path = path_key(phone.official_url or phone.source_url)
    best: tuple[int, dict[str, Any] | None, str] = (0, None, "")
    for row in rows:
        row_paths = [path_key(value) for value in candidate_urls(source, row)]
        row_names = [normalise_name(value) for value in candidate_names(source, row)]
        score = 0
        match_type = ""
        if phone_path and phone_path in row_paths:
            score = 40
            match_type = "official_path"
        if phone_name and phone_name in row_names:
            score = max(score, 30)
            match_type = "exact_model"
        if not match_type:
            for candidate in row_names:
                if not candidate.startswith(phone_name):
                    continue
                suffix = candidate[len(phone_name):]
                if suffix and len(suffix) <= 6 and suffix not in VARIANT_SUFFIX_TOKENS:
                    score = max(score, 15)
                    match_type = "model_with_colour"
        if score > best[0]:
            best = (score, row, match_type)
    return best[1], best[2]


def flatten_specs(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from flatten_specs(child, (*path, str(key)))
    elif isinstance(value, list):
        for child in value:
            yield from flatten_specs(child, path)
    elif value is not None and str(value).strip():
        yield path, str(value).strip()


def structured_evidence(row: dict[str, Any], source: LocalSource) -> str:
    lines = [f"{' | '.join(path)}: {value}" for path, value in flatten_specs(row.get("specs", {}))]
    for key in ("brief", "sku_name", "original_name"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
    return "\n".join(lines)


def structured_cpu(row: dict[str, Any]) -> str | None:
    """Some official tables put the chipset name in the group title."""
    specs = row.get("specs", {})
    if not isinstance(specs, dict):
        return None
    for group, value in specs.items():
        title = str(group).strip()
        if any(token in title for token in ("充电芯片", "影像芯片", "显示芯片", "电量计芯片")):
            continue
        child_labels = "/".join(path[-1] for path, _ in flatten_specs(value)) if value else ""
        haystack = f"{title} {child_labels}"
        if not re.search(r"(?:骁龙|天玑|麒麟|Exynos|O\d|A\d{2}|处理器)", haystack, re.IGNORECASE):
            continue
        if not re.search(r"(?:CPU|SoC|处理器|移动平台)", haystack, re.IGNORECASE):
            continue
        clean = re.sub(r"^(?:移动平台|处理器|芯片)\s*[:：]?\s*", "", title)
        if len(clean) >= 4:
            return clean[:120]
    return None


def extracted_fields(row: dict[str, Any], source: LocalSource) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    text = structured_evidence(row, source)
    specs = {key: value for key, value in parse_official_specs(text).items() if value is not None}
    if "cpu" not in specs:
        cpu = structured_cpu(row)
        if cpu:
            parsed_cpu = parse_official_specs(f"CPU型号: {cpu}")
            if parsed_cpu.get("cpu"):
                specs["cpu"] = parsed_cpu["cpu"]
    variants = parse_memory_variants(text)
    release_date = parse_release_date(text)
    return specs, variants, release_date


def import_source(db, source: LocalSource, *, dry_run: bool) -> dict[str, Any]:
    rows = json.loads(source.path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{source.path} 不是手机列表 JSON")
    brand = db.scalar(select(Brand).where(Brand.name == source.brand))
    if brand is None:
        return {"brand": source.brand, "matched": 0, "updated": 0, "fields_filled": {}, "variants_added": 0}
    phones = db.scalars(
        select(PhoneModel)
        .options(selectinload(PhoneModel.variants))
        .where(PhoneModel.brand_id == brand.id, PhoneModel.is_active.is_(True))
    ).unique().all()
    result: dict[str, Any] = {
        "brand": source.brand,
        "matched": 0,
        "updated": 0,
        "fields_filled": {field: 0 for field in UPDATABLE_FIELDS},
        "variants_added": 0,
        "matches": [],
    }
    for phone in phones:
        row, match_type = match_record(phone, source, rows)
        if row is None:
            continue
        result["matched"] += 1
        specs, variants, release_date = extracted_fields(row, source)
        changed = False
        for field in UPDATABLE_FIELDS:
            value = specs.get(field)
            if value is not None and (getattr(phone, field) is None or str(getattr(phone, field)).strip() == ""):
                setattr(phone, field, value)
                result["fields_filled"][field] += 1
                changed = True
        if release_date is not None and phone.release_date is None:
            phone.release_date = release_date
            changed = True
        if specs.get("main_camera_mp") and not phone.camera_summary:
            phone.camera_summary = f"官网结构化采集标注 {specs['main_camera_mp']:g} MP 主摄"
            changed = True
        existing_variants = {(item.ram_gb, item.storage_gb) for item in phone.variants}
        variants_added = 0
        added_variant_names: list[str] = []
        if match_type in {"official_path", "exact_model"}:
            for parsed in variants:
                key = (parsed.get("ram_gb"), parsed.get("storage_gb"))
                if key in existing_variants:
                    continue
                storage = parsed.get("storage_gb")
                if storage is None:
                    continue
                ram = parsed.get("ram_gb")
                db.add(PhoneVariant(
                    model_id=phone.id,
                    ram_gb=ram,
                    storage_gb=int(storage),
                    variant_name=str(parsed.get("variant_name") or (f"{ram}GB+{storage}GB" if ram else f"{storage}GB")),
                    is_active=True,
                ))
                existing_variants.add(key)
                variants_added += 1
                added_variant_names.append(str(parsed.get("variant_name") or (f"{ram}GB+{storage}GB" if ram else f"{storage}GB")))
        if changed or variants_added:
            phone.source_checked_at = datetime.now(timezone.utc)
            variant_count = len(existing_variants)
            merged_specs = {field: getattr(phone, field) for field in ("cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "weight_g")}
            phone.data_quality = "official_verified" if completeness(merged_specs) >= 66.7 and variant_count else "official_partial"
            result["updated"] += 1
            result["variants_added"] += variants_added
        if changed or variants_added:
            result["matches"].append({
                "model": phone.model_name,
                "source_model": row.get(source.name_key),
                "match_type": match_type,
                "fields": sorted(specs),
                "values": {field: specs[field] for field in sorted(specs)},
                "variants_added": variants_added,
                "variants": added_variant_names[:20],
            })
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="把项目内已有的品牌官网结构化采集结果补入 SQLite，不导入当前售价作为发售价")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backup_path: Path | None = None
    if not args.dry_run:
        database_path = Path(engine.url.database or "")
        if database_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = DATA_DIR / "backups" / f"before-local-crawl-{stamp}.db"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(database_path, backup_path)
    results: list[dict[str, Any]] = []
    with SessionLocal() as db:
        for source in SOURCES:
            if not source.path.is_file():
                continue
            results.append(import_source(db, source, dry_run=args.dry_run))
    if not args.dry_run:
        checkpoint_database()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "backup": str(backup_path.resolve()) if backup_path else None,
        "sources": [str(item.path.relative_to(PROJECT_ROOT)) for item in SOURCES if item.path.is_file()],
        "results": results,
        "totals": {
            "matched": sum(item["matched"] for item in results),
            "updated": sum(item["updated"] for item in results),
            "variants_added": sum(item["variants_added"] for item in results),
            "fields_filled": {
                field: sum(item["fields_filled"].get(field, 0) for item in results)
                for field in UPDATABLE_FIELDS
            },
        },
    }
    DATA_DIR.joinpath("reports").mkdir(parents=True, exist_ok=True)
    output = DATA_DIR / "reports" / "local-crawl-import-latest.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))
    for item in results:
        print(f"{item['brand']}: matched={item['matched']} updated={item['updated']} variants_added={item['variants_added']}")
    print(f"Report: {output.resolve()}")


if __name__ == "__main__":
    main()
