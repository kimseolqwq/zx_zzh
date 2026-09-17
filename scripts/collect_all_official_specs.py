"""Collect non-price specifications for existing phones from official pages.

The crawl and import are deliberately separate.  A crawl never modifies the
database; --apply-report only fills empty model-level specification fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR
from app.crawlers.base import SafeFetcher
from app.crawlers.catalog_pipeline import read_sources
from app.crawlers.official_specs import (
    SPEC_BOUNDS, completeness, extract_official_image, extract_official_metadata,
    parse_official_specs, parse_release_date,
)
from app.database import SessionLocal, checkpoint_database, engine
from app.models import PhoneModel


FIELDS = (
    "release_date", "cpu", "screen_size", "screen_type", "resolution", "refresh_rate",
    "main_camera_mp", "battery_mah", "charging_w", "wireless_charging_w",
    "wireless_charging_supported", "weight_g", "thickness_mm", "waterproof",
    "waterproof_supported", "nfc", "five_g", "screen_shape", "telephoto",
    "operating_system",
    "image_url", "image_source_url",
)
OFFICIAL_HOSTS = {
    "Apple": "www.apple.com.cn", "华为": "consumer.huawei.com",
    "小米": "www.mi.com", "荣耀": "www.honor.com",
    "OPPO": "www.oppo.com", "vivo": "www.vivo.com.cn",
    "一加": "www.oneplus.com", "三星": "www.samsung.com.cn",
    "realme": "www.realme.com", "魅族": "detail.meizu.com",
}


def source_url(phone: PhoneModel, catalog: dict[tuple[str, str], str]) -> str | None:
    brand = phone.brand.name
    url = catalog.get((brand.casefold(), phone.model_name.casefold())) or phone.official_url
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != OFFICIAL_HOSTS.get(brand):
        return None
    # Product index pages and root URLs contain many different phones.
    if len([part for part in parsed.path.split("/") if part]) < 2:
        return None
    return url


def page_matches(name: str, requested_url: str, final_url: str, html: str) -> bool:
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    if requested.hostname != final.hostname or requested.path.rstrip("/") != final.path.rstrip("/"):
        return False
    soup = BeautifulSoup(html, "html.parser")
    headings = " ".join(tag.get_text(" ", strip=True) for tag in soup.select("h1, title")[:6])
    compact = lambda value: re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())
    target = compact(name)
    for prefix in ("huawei", "samsung", "oppo", "vivo"):
        if target.startswith(prefix):
            target = target[len(prefix):]
            break
    if len(target) >= 5 and target in compact(headings):
        return True
    # Some official sites render the model name dynamically and leave the
    # document title generic.  The requested and final paths are already the
    # same here, so an explicit model slug is a safe fallback.
    return len(target) >= 5 and target in compact(final.path)


def screen_type(text: str) -> str | None:
    # Require a nearby display label: an isolated OLED in navigation is not evidence.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line not in {"屏幕", "显示屏", "屏幕显示", "显示", "Display"}:
            continue
        section = lines[index + 1:index + 18]
        for position, candidate in enumerate(section):
            if candidate in {"类型", "屏幕类型", "屏幕材质", "面板类型"} and position + 1 < len(section):
                match = re.search(r"\b(LTPO\s*OLED|AMOLED|OLED|LCD)\b", section[position + 1], re.IGNORECASE)
                if match:
                    return re.sub(r"\s+", " ", match.group(1).upper())
    patterns = (
        r"(?:屏幕|显示屏)[^\n]{0,30}\n(?:[^\n]{0,55}\n){0,3}[^\n]{0,55}\b(LTPO\s*OLED|AMOLED|OLED|LCD)\b",
        r"(?:屏幕类型|显示屏类型|屏幕材质|面板类型)\s*[:：]?\s*\n?\s*(LTPO\s*OLED|AMOLED|OLED|LCD)\b",
        r"\b(LTPO\s*OLED|AMOLED|OLED|LCD)\s*(?:全面屏|显示屏|屏幕)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1).upper())
    return None


def extract_fields(html: str, visible_text: str, final_url: str, name: str) -> dict:
    text = visible_text + "\n" + extract_official_metadata(html)
    fields = {key: value for key, value in parse_official_specs(text).items() if value is not None}
    if re.search(r"(?:上市时间|发布时间|正式发布)\s*[:：]?\s*20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", text):
        release = parse_release_date(text)
        if release:
            fields["release_date"] = release.isoformat()
    kind = screen_type(visible_text)
    if kind:
        fields["screen_type"] = kind
    image = extract_official_image(html, final_url, name)
    if image and image["image_url"].startswith("https://"):
        fields.update(image)
    return fields


def refresh_report(path: Path) -> None:
    """Re-extract from saved official evidence without another network request."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        for row in payload["records"]:
            if row["status"] != "matched":
                continue
            evidence = ROOT / row["evidence"]
            html_path = evidence.with_suffix(".html")
            if not evidence.is_file() or not html_path.is_file():
                continue
            html = html_path.read_text(encoding="utf-8")
            if row.get("cached_sha256") and hashlib.sha256(html_path.read_bytes()).hexdigest() != row["cached_sha256"]:
                continue
            row["cached_sha256"] = hashlib.sha256(html_path.read_bytes()).hexdigest()
            phone = db.get(PhoneModel, row["phone_id"])
            if phone is None or phone.model_name != row["model"]:
                continue
            fields = extract_fields(html, evidence.read_text(encoding="utf-8"), row["url"], row["model"])
            row["fields"] = {
                key: value for key, value in fields.items()
                if key in FIELDS and getattr(phone, key) in (None, "")
            }
    payload["summary"]["field_proposals"] = dict(Counter(key for row in payload["records"] for key in row["fields"]))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


def crawl(args: argparse.Namespace) -> None:
    catalog = {
        (item.brand.casefold(), item.model_name.casefold()): item.official_url
        for item in read_sources(ROOT / "config" / "official_catalog_sources.csv")
    }
    with SessionLocal() as db:
        phones = db.scalars(select(PhoneModel).options(selectinload(PhoneModel.brand)).where(
            PhoneModel.is_active.is_(True)
        ).order_by(PhoneModel.id)).all()
        targets = [
            (phone.id, phone.brand.name, phone.model_name, source_url(phone, catalog),
             {field: getattr(phone, field) for field in FIELDS})
            for phone in phones if not args.brand or phone.brand.name.casefold() == args.brand.casefold()
        ]
    fetcher = SafeFetcher(minimum_interval=max(1.5, args.interval))
    records: list[dict] = []
    for index, (phone_id, brand, name, url, existing) in enumerate(targets[:args.limit], 1):
        record = {"phone_id": phone_id, "brand": brand, "model": name, "url": url, "fields": {}}
        try:
            if not url:
                raise ValueError("缺少机型专属官网 HTTPS 页面")
            page = fetcher.fetch(url)
            if not page_matches(name, url, page.url, page.html):
                raise ValueError("跳转地址或页面标题与目标型号不匹配")
            fields = extract_fields(page.html, page.visible_text, page.url, name)
            record["fields"] = {
                field: value for field, value in fields.items()
                if field in FIELDS and (existing[field] is None or existing[field] == "")
            }
            record["evidence"] = page.text_path.relative_to(ROOT).as_posix()
            record["sha256"] = page.sha256
            record["cached_sha256"] = hashlib.sha256(page.html_path.read_bytes()).hexdigest()
            record["fetched_at"] = page.fetched_at.isoformat()
            record["status"] = "matched"
        except Exception as exc:
            record["status"] = "skipped"
            record["reason"] = str(exc)[:250]
        records.append(record)
        if index % 20 == 0 or index == len(targets[:args.limit]):
            print(f"{index}/{len(targets[:args.limit])}: matched={sum(r['status'] == 'matched' for r in records)}", flush=True)
    output = args.report or DATA_DIR / "reports" / f"spec-crawl-{datetime.now():%Y%m%d-%H%M%S}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "records": records,
               "summary": {"attempted": len(records), "matched": sum(r["status"] == "matched" for r in records),
                           "field_proposals": dict(Counter(k for r in records for k in r["fields"]))}}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {output.resolve()}\n{payload['summary']}", flush=True)


def apply_report(path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    records = report["records"]
    backup = DATA_DIR / "backups" / f"before-spec-import-{datetime.now():%Y%m%d-%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    database = Path(engine.url.database or "")
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    counts: Counter[str] = Counter()
    with SessionLocal() as db:
        for row in records:
            if row["status"] != "matched" or not row["fields"]:
                continue
            phone = db.get(PhoneModel, row["phone_id"])
            if phone is None or phone.model_name != row["model"] or phone.brand.name != row["brand"]:
                continue
            evidence = (ROOT / row["evidence"]).resolve()
            if not evidence.is_relative_to((DATA_DIR / "raw").resolve()) or not evidence.is_file():
                continue
            html_path = evidence.with_suffix(".html")
            if not html_path.is_file():
                continue
            html = html_path.read_text(encoding="utf-8")
            if hashlib.sha256(html_path.read_bytes()).hexdigest() != row.get("cached_sha256"):
                continue
            verified = extract_fields(html, evidence.read_text(encoding="utf-8"), row["url"], row["model"])
            changed = False
            for field, value in row["fields"].items():
                if field not in FIELDS or value is None or getattr(phone, field) not in (None, ""):
                    continue
                if verified.get(field) != value:
                    continue
                if field in SPEC_BOUNDS and not SPEC_BOUNDS[field][0] <= float(value) <= SPEC_BOUNDS[field][1]:
                    continue
                if field.endswith("_url") and (not isinstance(value, str) or not value.startswith("https://")):
                    continue
                if field == "release_date":
                    value = date.fromisoformat(value)
                setattr(phone, field, value)
                counts[field] += 1
                changed = True
            if changed:
                phone.source_checked_at = datetime.fromisoformat(row["fetched_at"])
                if phone.official_url != row["url"]:
                    phone.official_url = row["url"]
                phone.source_url = row["url"]
                specs = {key: getattr(phone, key) for key in ("cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "weight_g")}
                if phone.data_quality != "demo":
                    phone.data_quality = "official_verified" if completeness(specs) >= 66.7 and phone.variants else "official_partial"
        db.commit()
    checkpoint_database()
    print(json.dumps({"backup": str(backup.resolve()), "filled": dict(counts)}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="采集现有手机官网非价格参数；先采集报告，再审核入库")
    parser.add_argument("--brand", help="按品牌限定")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply-report", type=Path)
    parser.add_argument("--refresh-report", type=Path, help="复用已保存证据重新解析，不请求网络")
    args = parser.parse_args()
    if args.apply_report:
        apply_report(args.apply_report)
    elif args.refresh_report:
        refresh_report(args.refresh_report)
    else:
        crawl(args)


if __name__ == "__main__":
    main()
