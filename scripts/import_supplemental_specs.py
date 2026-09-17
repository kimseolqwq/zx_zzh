"""Import manually verified supplemental phone specifications with source evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATA_DIR
from app.crawlers.official_specs import SPEC_BOUNDS, is_likely_product_image_url
from app.database import SessionLocal, checkpoint_database, create_schema, engine
from app.models import PhoneModel


NUMERIC_FIELDS = {
    "screen_size": float,
    "refresh_rate": int,
    "main_camera_mp": float,
    "battery_mah": int,
    "charging_w": float,
    "wireless_charging_w": float,
    "weight_g": float,
    "thickness_mm": float,
}
TEXT_FIELDS = {
    "cpu", "screen_type", "resolution", "waterproof",
    "operating_system", "image_url", "image_source_url", "camera_summary",
    "screen_shape",
}
BOOLEAN_FIELDS = {
    "wireless_charging_supported", "waterproof_supported", "nfc", "five_g", "telephoto",
}


def evidence_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.casefold() != ".html":
        return raw
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    return soup.get_text(" ", strip=True) + "\n" + raw


def valid_evidence_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    raw_root = (DATA_DIR / "raw").resolve()
    if not path.is_relative_to(raw_root) or not path.is_file():
        raise ValueError(f"证据文件不在 data/raw 或不存在：{value}")
    return path


def convert_value(field: str, value: object) -> object:
    if field in NUMERIC_FIELDS:
        return NUMERIC_FIELDS[field](value)
    if field == "release_date":
        return date.fromisoformat(str(value))
    if field in BOOLEAN_FIELDS:
        if isinstance(value, bool):
            return value
        text = str(value).strip().casefold()
        if text in {"1", "true", "yes", "支持"}:
            return True
        if text in {"0", "false", "no", "不支持"}:
            return False
        raise ValueError(f"无效布尔值：{value}")
    if field in TEXT_FIELDS:
        return str(value).strip()
    raise ValueError(f"不支持的字段：{field}")


def import_report(path: Path) -> None:
    path = path.resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    create_schema()
    backup = DATA_DIR / "backups" / f"before-supplemental-specs-{datetime.now():%Y%m%d-%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    database = Path(engine.url.database or "")
    if database.exists():
        checkpoint_database()
        shutil.copy2(database, backup)

    counts: dict[str, int] = {}
    verified_evidence: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    with SessionLocal() as db:
        for record in payload["records"]:
            phone = db.scalars(
                select(PhoneModel)
                .options(selectinload(PhoneModel.brand))
                .where(
                    PhoneModel.model_name == record["model"],
                    PhoneModel.is_active.is_(True),
                )
            ).all()
            phone = next(
                (item for item in phone if item.brand.name == record["brand"]),
                None,
            )
            if phone is None:
                skipped.append({"phone": f"{record['brand']} {record['model']}", "reason": "数据库中未找到"})
                continue
            changed = False
            for field, item in record["fields"].items():
                if getattr(phone, field) not in (None, ""):
                    continue
                evidence_path = valid_evidence_path(item["evidence"])
                snippets = [str(value) for value in item.get("snippets") or [item["snippet"]]]
                text = evidence_text(evidence_path)
                missing_snippets = [snippet for snippet in snippets if snippet not in text]
                if missing_snippets:
                    skipped.append({
                        "phone": f"{record['brand']} {record['model']}",
                        "field": field,
                        "reason": f"证据文本未包含：{', '.join(missing_snippets)}",
                    })
                    continue
                try:
                    value = convert_value(field, item["value"])
                except (TypeError, ValueError) as exc:
                    skipped.append({
                        "phone": f"{record['brand']} {record['model']}",
                        "field": field,
                        "reason": str(exc),
                    })
                    continue
                if field in SPEC_BOUNDS and not SPEC_BOUNDS[field][0] <= float(value) <= SPEC_BOUNDS[field][1]:
                    skipped.append({
                        "phone": f"{record['brand']} {record['model']}",
                        "field": field,
                        "reason": f"超出合理范围：{value}",
                    })
                    continue
                if field == "image_url" and not is_likely_product_image_url(str(value)):
                    skipped.append({
                        "phone": f"{record['brand']} {record['model']}",
                        "field": field,
                        "reason": "图片 URL 未通过产品图校验",
                    })
                    continue
                setattr(phone, field, value)
                counts[field] = counts.get(field, 0) + 1
                rel = evidence_path.relative_to(PROJECT_ROOT).as_posix()
                verified_evidence[rel] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
                changed = True
            if changed:
                phone.source_checked_at = datetime.now(timezone.utc)
        db.commit()
    checkpoint_database()

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": path.relative_to(PROJECT_ROOT).as_posix(),
        "backup": backup.relative_to(PROJECT_ROOT).as_posix() if backup.exists() else None,
        "filled": counts,
        "evidence_sha256": verified_evidence,
        "skipped": skipped,
    }
    output = DATA_DIR / "reports" / "supplemental-specs-import-latest.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="导入人工核验的补充规格并校验原文证据")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    import_report(args.report)


if __name__ == "__main__":
    main()
