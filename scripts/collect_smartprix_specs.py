"""Collect missing non-price specifications from Smartprix's public pages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATA_DIR
from app.crawlers.base import SafeFetcher
from app.crawlers.official_specs import SPEC_BOUNDS, is_likely_product_image_url
from app.database import SessionLocal, checkpoint_database, engine
from app.models import PhoneModel
from scripts.collect_third_party_specs import (
    FIELDS,
    _match_tokens,
    boolean_value,
    first_number,
    parse_release,
)


SITEMAP_URL = "https://www.smartprix.com/sitemaps/in/mobiles.xml"
SITEMAP_CACHE = DATA_DIR / "cache" / "smartprix-mobile-urls.json"
SITEMAP_CACHE_TTL = timedelta(days=7)


def sitemap_product_urls(xml_text: str) -> list[str]:
    urls = re.findall(r"<loc>(.*?)</loc>", xml_text)
    products = []
    for value in urls:
        url = value.strip().rstrip("/")
        if re.fullmatch(r"https://www\.smartprix\.com/mobiles/[^/?#]+", url):
            products.append(url)
    return list(dict.fromkeys(products))


def clean_product_slug(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"-p[a-z0-9]{6,}$", "", slug, flags=re.IGNORECASE)


def sitemap_match_urls(phone: PhoneModel, product_urls: list[str]) -> list[str]:
    expected = _match_tokens(f"{phone.brand.name} {phone.model_name}")
    if not expected:
        return []
    model_has_capacity = bool(
        re.search(r"(?<![a-z0-9])\d+(?:gb|tb)(?![a-z0-9])", phone.model_name, re.IGNORECASE)
    )
    expected_network = ""
    if re.search(r"(?<![a-z0-9])5g(?![a-z0-9])", phone.model_name, re.IGNORECASE):
        expected_network = "5g"
    elif re.search(r"(?<![a-z0-9])4g(?![a-z0-9])", phone.model_name, re.IGNORECASE):
        expected_network = "4g"
    matches: list[tuple[int, str]] = []
    for url in product_urls:
        slug = clean_product_slug(url)
        if not model_has_capacity and re.search(
            r"(?<![a-z0-9])\d+(?:gb|tb)(?![a-z0-9])", slug, re.IGNORECASE
        ):
            continue
        if _match_tokens(slug) != expected:
            continue
        slug_network = ""
        if re.search(r"(?<![a-z0-9])5g(?![a-z0-9])", slug, re.IGNORECASE):
            slug_network = "5g"
        elif re.search(r"(?<![a-z0-9])4g(?![a-z0-9])", slug, re.IGNORECASE):
            slug_network = "4g"
        if expected_network:
            rank = 0 if slug_network == expected_network else 1 if not slug_network else 2
        else:
            rank = 0 if not slug_network else 1 if slug_network == "5g" else 2
        matches.append((rank, url))
    return [
        url for _, url in sorted(
            matches,
            key=lambda value: (value[0], len(clean_product_slug(value[1])), value[1]),
        )
    ]


def load_sitemap_urls(fetcher: SafeFetcher) -> list[str]:
    now = datetime.now(timezone.utc)
    if SITEMAP_CACHE.is_file():
        try:
            cached = json.loads(SITEMAP_CACHE.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if now - fetched_at <= SITEMAP_CACHE_TTL and cached.get("urls"):
                return list(cached["urls"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass

    result = fetcher.fetch(SITEMAP_URL)
    urls = sitemap_product_urls(result.html)
    if not urls:
        raise ValueError("Smartprix sitemap 未返回有效产品页")
    SITEMAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SITEMAP_CACHE.write_text(json.dumps({
        "fetched_at": now.isoformat(),
        "source_url": SITEMAP_URL,
        "evidence": result.text_path.relative_to(PROJECT_ROOT).as_posix(),
        "urls": urls,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return urls


def table_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in soup.select("table"):
        current: dict[str, str] = {}
        for tr in table.select("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip().casefold()
            value = re.sub(r"\s+", " ", " ".join(
                cell.get_text(" ", strip=True) for cell in cells[1:]
            )).strip()
            if label and value:
                current[label] = value
        if current:
            rows.append(current)
    return rows


def _row_with(
    rows: list[dict[str, str]],
    *labels: str,
    contains: str | None = None,
) -> dict[str, str]:
    for row in rows:
        if not all(label in row for label in labels):
            continue
        if contains and contains.casefold() not in " ".join(row.values()).casefold():
            continue
        return row
    return {}


def extract_fields(html: str, final_url: str = "") -> tuple[dict[str, object], dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    rows = table_rows(soup)
    fields: dict[str, object] = {}
    source_labels: dict[str, str] = {}

    def set_field(field: str, value: object, label: str) -> None:
        if value is not None and value != "":
            fields[field] = value
            source_labels[field] = label

    general = _row_with(rows, "release date")
    if general:
        set_field("release_date", parse_release(general["release date"]), "Release Date")

    os_row = _row_with(rows, "chipset")
    if os_row:
        set_field("cpu", os_row.get("chipset"), "Chipset")
        set_field("operating_system", os_row.get("os"), "OS")

    display = _row_with(rows, "size", contains="inch")
    if not display:
        display = _row_with(rows, "type", contains="screen")
    if display:
        display_type = display.get("type", "")
        panel = re.search(r"(LTPO\s*AMOLED|AMOLED|OLED|LCD|IPS)", display_type, re.IGNORECASE)
        if panel:
            set_field("screen_type", re.sub(r"\s+", " ", panel.group(1)).upper(), "Display Type")
        display_text = " ".join(display.values()).casefold()
        if "fold" in display_text or "折叠" in display_text:
            set_field("screen_shape", "foldable", "Display shape")
        elif "curved" in display_text or "曲面" in display_text:
            set_field("screen_shape", "curved", "Display shape")
        elif "flat" in display_text or "直屏" in display_text:
            set_field("screen_shape", "flat", "Display shape")
        size_text = display.get("size", "")
        size = re.search(r"(\d+(?:\.\d+)?)\s*(?:inch|inches)", size_text, re.IGNORECASE)
        if size:
            set_field("screen_size", float(size.group(1)), "Display Size")
        resolution = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", size_text, re.IGNORECASE)
        if resolution:
            set_field(
                "resolution",
                f"{resolution.group(1)}x{resolution.group(2)}",
                "Display Resolution",
            )
        set_field("refresh_rate", first_number(size_text, "hz"), "Refresh Rate")

    camera = _row_with(rows, "rear camera")
    if camera:
        rear = camera["rear camera"]
        megapixels = re.search(r"(\d+(?:\.\d+)?)\s*mp", rear, re.IGNORECASE)
        if megapixels:
            camera_mp = float(megapixels.group(1))
            set_field("main_camera_mp", camera_mp, "Rear Camera")
            fields["_camera_summary"] = f"{rear[:220]}（Smartprix 规格页核对）"
        if any(token in rear.casefold() for token in ("telephoto", "periscope", "长焦", "潜望")):
            set_field("telephoto", True, "Telephoto camera")

    weight_row = _row_with(rows, "weight")
    if weight_row:
        set_field("weight_g", first_number(weight_row["weight"], "g"), "Weight")

    battery = _row_with(rows, "size", contains="mah")
    if battery:
        set_field("battery_mah", first_number(battery["size"], "mah"), "Battery Capacity")
        set_field("charging_w", first_number(battery.get("fast charging", ""), "w"), "Fast Charging")
        wireless = battery.get("wireless charging", "")
        if wireless:
            wireless_flag = boolean_value(wireless)
            if wireless_flag is not None:
                set_field("wireless_charging_supported", wireless_flag, "Wireless Charging")
            if re.search(r"\b(?:no|not supported)\b", wireless, re.IGNORECASE):
                set_field("wireless_charging_w", 0.0, "Wireless Charging: No")
            else:
                set_field("wireless_charging_w", first_number(wireless, "w"), "Wireless Charging")

    thickness_row = _row_with(rows, "thickness")
    if thickness_row:
        set_field("thickness_mm", first_number(thickness_row["thickness"], "mm"), "Thickness")
    if not fields.get("thickness_mm"):
        thickness = re.search(
            r"\bthickness\s*[:：]?\s*(\d+(?:\.\d+)?)\s*mm\b",
            page_text,
            re.IGNORECASE,
        )
        if thickness:
            set_field("thickness_mm", float(thickness.group(1)), "Thickness text")

    water = _row_with(rows, "ip rating")
    waterproof_text = water.get("ip rating", "") if water else ""
    if not waterproof_text:
        water = _row_with(rows, "water resistance")
        waterproof_text = water.get("water resistance", "") if water else ""
    ip_match = re.search(
        r"\bIP(?:68|69|69K|67|66|65|64)(?:/IP(?:68|69|69K|67|66|65|64))?\b",
        waterproof_text or page_text,
        re.IGNORECASE,
    )
    if ip_match:
        set_field("waterproof", ip_match.group(0).upper(), "IP Rating")
        set_field("waterproof_supported", True, "IP Rating")
    elif water:
        waterproof_flag = boolean_value(" ".join(water.values()))
        if waterproof_flag is not None:
            set_field("waterproof_supported", waterproof_flag, "Water Resistance")

    nfc_row = _row_with(rows, "nfc")
    nfc_flag = boolean_value(nfc_row.get("nfc", "")) if nfc_row else None
    if nfc_flag is not None:
        set_field("nfc", nfc_flag, "NFC")
    five_g_row = next((row for row in rows if "5g" in row), {})
    five_g_flag = boolean_value(five_g_row.get("5g", "")) if five_g_row else None
    if five_g_flag is None:
        network_text = " ".join(
            value for row in rows for key, value in row.items() if "network" in key
        )
        if re.search(r"(?<![a-z0-9])5g(?![a-z0-9])", network_text, re.IGNORECASE):
            five_g_flag = True
    if five_g_flag is not None:
        set_field("five_g", five_g_flag, "5G Network")

    image = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
    image_url = (image.get("content") or "").strip() if image else ""
    if image_url.startswith("https://") and is_likely_product_image_url(image_url):
        set_field("image_url", image_url, "Social product image")
        if final_url:
            set_field("image_source_url", final_url, "Product page")
    return fields, source_labels


def crawl(
    *,
    limit: int | None = None,
    interval: float = 1.5,
    brand: str | None = None,
    report: Path | None = None,
) -> None:
    with SessionLocal() as db:
        phones = db.scalars(
            select(PhoneModel)
            .options(selectinload(PhoneModel.brand))
            .where(PhoneModel.is_active.is_(True))
            .order_by(PhoneModel.id)
        ).all()
    targets = [
        phone for phone in phones
        if (not brand or phone.brand.name.casefold() == brand.casefold())
        and any(getattr(phone, field) is None or str(getattr(phone, field)).strip() == "" for field in FIELDS)
    ][:limit]
    fetcher = SafeFetcher(minimum_interval=max(1.5, interval))
    try:
        product_urls = load_sitemap_urls(fetcher)
    except Exception as exc:
        print(f"Smartprix sitemap 不可用：{exc}", flush=True)
        product_urls = []
    records: list[dict] = []
    for index, phone in enumerate(targets, 1):
        record = {
            "phone_id": phone.id,
            "brand": phone.brand.name,
            "model": phone.model_name,
            "source": "https://www.smartprix.com/",
            "fields": {},
            "source_labels": {},
        }
        try:
            matches = sitemap_match_urls(phone, product_urls)
            if not matches:
                raise ValueError("未找到精确型号页")
            result = None
            chosen_url = ""
            for url in matches:
                result = fetcher.fetch(url)
                chosen_url = url
                break
            if result is None or not sitemap_match_urls(
                phone, [str(result.url).rstrip("/")]
            ):
                raise ValueError("详情页跳转后的型号与 sitemap 目标不匹配")
            fields, labels = extract_fields(result.html, str(result.url))
            fields = {key: value for key, value in fields.items() if key in FIELDS}
            record.update({
                "url": str(result.url),
                "requested_url": chosen_url,
                "match_source": "sitemap",
                "evidence": str(result.text_path.relative_to(PROJECT_ROOT).as_posix()),
                "cached_sha256": hashlib.sha256(result.html_path.read_bytes()).hexdigest(),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "fields": fields,
                "source_labels": labels,
                "status": "matched",
            })
        except Exception as exc:
            record["status"] = "skipped"
            record["reason"] = str(exc)[:240]
        records.append(record)
        if index % 20 == 0 or index == len(targets):
            print(
                f"{index}/{len(targets)}: matched={sum(row['status'] == 'matched' for row in records)}",
                flush=True,
            )
    output = report or DATA_DIR / "reports" / f"smartprix-spec-crawl-{datetime.now():%Y%m%d-%H%M%S}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Smartprix (third-party fallback)",
        "records": records,
        "summary": {
            "attempted": len(records),
            "matched": sum(row["status"] == "matched" for row in records),
            "field_proposals": {
                field: sum(field in row["fields"] for row in records)
                for field in FIELDS
            },
        },
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(output.resolve())


def apply_report(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    backup = PROJECT_ROOT / "data" / "backups" / f"before-smartprix-specs-{datetime.now():%Y%m%d-%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    database = Path(engine.url.database or "")
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    counts: dict[str, int] = {field: 0 for field in FIELDS}
    with SessionLocal() as db:
        for row in payload["records"]:
            if row.get("status") != "matched" or not row.get("fields"):
                continue
            phone = db.get(PhoneModel, row["phone_id"])
            if phone is None or phone.model_name != row["model"]:
                continue
            evidence_text = PROJECT_ROOT / row["evidence"]
            evidence_html = evidence_text.with_suffix(".html")
            if not evidence_text.is_file() or not evidence_html.is_file():
                continue
            if hashlib.sha256(evidence_html.read_bytes()).hexdigest() != row["cached_sha256"]:
                continue
            verified, _ = extract_fields(
                evidence_html.read_text(encoding="utf-8"),
                row.get("url", ""),
            )
            changed = False
            for field, value in row["fields"].items():
                if field not in FIELDS or value in (None, ""):
                    continue
                if getattr(phone, field) not in (None, ""):
                    continue
                if verified.get(field) != value:
                    continue
                if field in SPEC_BOUNDS and not SPEC_BOUNDS[field][0] <= float(value) <= SPEC_BOUNDS[field][1]:
                    continue
                if field == "image_url" and not is_likely_product_image_url(str(value)):
                    continue
                if field == "release_date":
                    value = date.fromisoformat(str(value))
                setattr(phone, field, value)
                counts[field] += 1
                changed = True
            if phone.main_camera_mp and not phone.camera_summary:
                summary = verified.get("_camera_summary")
                if isinstance(summary, str) and summary:
                    phone.camera_summary = summary
                    counts["camera_summary"] = counts.get("camera_summary", 0) + 1
                    changed = True
            if changed:
                phone.source_checked_at = datetime.now(timezone.utc)
        db.commit()
    checkpoint_database()
    print(json.dumps({"backup": str(backup.resolve()), "filled": counts}, ensure_ascii=False, indent=2))


def refresh_report(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        for row in payload["records"]:
            if row.get("status") != "matched":
                continue
            phone = db.get(PhoneModel, row["phone_id"])
            if phone is None or phone.model_name != row["model"]:
                continue
            evidence_text = PROJECT_ROOT / row["evidence"]
            evidence_html = evidence_text.with_suffix(".html")
            if not evidence_text.is_file() or not evidence_html.is_file():
                continue
            if hashlib.sha256(evidence_html.read_bytes()).hexdigest() != row["cached_sha256"]:
                continue
            fields, labels = extract_fields(
                evidence_html.read_text(encoding="utf-8"),
                row.get("url", ""),
            )
            row["fields"] = {
                field: value for field, value in fields.items()
                if field in FIELDS and getattr(phone, field) in (None, "")
            }
            row["source_labels"] = labels
    payload["summary"]["field_proposals"] = {
        field: sum(field in row["fields"] for row in payload["records"])
        for field in FIELDS
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Smartprix 第三方规格回退采集：只填空字段并保留来源报告")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--brand")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply-report", type=Path)
    parser.add_argument("--refresh-report", type=Path)
    args = parser.parse_args()
    if args.apply_report:
        apply_report(args.apply_report)
    elif args.refresh_report:
        refresh_report(args.refresh_report)
    else:
        crawl(
            limit=args.limit,
            interval=args.interval,
            brand=args.brand,
            report=args.report,
        )


if __name__ == "__main__":
    main()
