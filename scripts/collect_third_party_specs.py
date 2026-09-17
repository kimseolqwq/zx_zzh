"""Collect missing non-price specs from an explicitly allowed third-party site."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.base import SafeFetcher
from app.crawlers.official_specs import SPEC_BOUNDS
from app.config import DATA_DIR
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
BRAND_SLUGS = {
    "Apple": ("apple",),
    "华为": ("huawei",),
    "小米": ("xiaomi",),
    "荣耀": ("honor",),
    "OPPO": ("oppo",),
    "vivo": ("vivo",),
    "一加": ("oneplus", "one-plus"),
    "三星": ("samsung",),
    "realme": ("realme",),
    "魅族": ("meizu",),
}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
SITEMAP_URL = "https://www.mobiledokan.com/sitemap/mobile.xml"
SITEMAP_CACHE = DATA_DIR / "cache" / "mobiledokan-mobile-urls.json"
SITEMAP_CACHE_TTL = timedelta(days=7)
MATCH_REPLACEMENTS = {
    "保时捷设计": " porsche design ",
    "非凡大师": " ultimate ",
    "至尊版": " ultra ",
    "竞速版": " racing ",
    "活力版": " energy edition ",
    "超级版": " super ",
    "焕新版": " refresh edition ",
    "卫星通信版": " satellite ",
    "畅玩": " play ",
    "元气版": " genki ",
    "真我": " realme ",
    "荣耀": " honor ",
    "华为": " huawei ",
    "三星": " samsung ",
    "小米": " xiaomi ",
    "一加": " oneplus ",
    "魅族": " meizu ",
}
IGNORABLE_MATCH_TOKENS = {
    "4g", "5g", "nfc", "china", "cn", "global", "india", "indian", "edition",
}


def compact(value: str) -> str:
    text = value.casefold()
    for aliases in BRAND_SLUGS.values():
        for alias in aliases:
            text = text.replace(alias.replace("-", ""), "")
    for token in ("荣耀", "华为", "小米", "三星", "一加", "魅族", "真我"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def model_slug(name: str, brand: str) -> str:
    value = name.casefold()
    if value.startswith("iqoo"):
        value = value[4:].strip()
    prefixes = [brand.casefold(), *BRAND_SLUGS.get(brand, ()), "真我", "魅族", "一加", "荣耀", "华为", "小米", "三星"]
    for prefix in sorted(prefixes, key=len, reverse=True):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.replace("+", " plus ")
    value = value.replace("至尊版", " ultra ")
    value = value.replace("竞速版", " racing ")
    value = value.replace("元气版", " genki ")
    value = value.replace("超级版", " super ")
    value = value.replace("保时捷设计", " porsche design ")
    value = value.replace("非凡大师", " ultimate ")
    value = value.replace("卫星通信版", " satellite ")
    value = value.replace("活力版", " energy ")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def slug_candidates(phone: PhoneModel) -> list[str]:
    model = model_slug(phone.model_name, phone.brand.name)
    if "iqoo" in phone.model_name.casefold():
        aliases = ("iqoo",)
    else:
        aliases = BRAND_SLUGS.get(phone.brand.name, (phone.brand.name.casefold(),))
    candidates = [f"{alias}-{model}" for alias in aliases]
    for suffix in ("-5g", "-4g"):
        if model.endswith(suffix):
            candidates.extend(f"{alias}-{model[:-len(suffix)]}" for alias in aliases)
    if phone.brand.name == "小米" and model.startswith("redmi-"):
        candidates.append(model)
    return list(dict.fromkeys(candidates))


def sitemap_product_urls(xml_text: str) -> list[str]:
    """Return only canonical MobileDokan product pages from its sitemap."""
    urls = re.findall(r"<loc>(.*?)</loc>", xml_text)
    product_urls = []
    for value in urls:
        url = value.strip().rstrip("/")
        if re.fullmatch(r"https://www\.mobiledokan\.com/mobile/[^/?#]+", url):
            product_urls.append(url)
    return list(dict.fromkeys(product_urls))


def _match_tokens(value: str) -> set[str]:
    text = value.casefold()
    text = text.replace("＋", " plus ").replace("+", " plus ")
    for source, target in MATCH_REPLACEMENTS.items():
        text = text.replace(source.casefold(), target)
    raw_tokens = re.findall(r"[a-z]+\d*[a-z]*|\d+[a-z]*", text)
    tokens: set[str] = set()
    index = 0
    while index < len(raw_tokens):
        current = raw_tokens[index]
        following = raw_tokens[index + 1] if index + 1 < len(raw_tokens) else ""
        if (
            re.fullmatch(r"[a-z]{1,8}", current)
            and re.fullmatch(r"\d{1,3}[a-z]*", following)
            and following not in {"4g", "5g"}
        ):
            tokens.add(current + following)
            index += 2
            continue
        tokens.add(current)
        index += 1
    tokens = {
        token for token in tokens
        if token not in IGNORABLE_MATCH_TOKENS
        and not re.fullmatch(r"(?:\d+gb|\d+tb)", token)
    }
    for token in list(tokens):
        if any(
            other != token
            and other.startswith(token)
            and re.match(r"\d", other[len(token):])
            for other in tokens
        ):
            tokens.discard(token)
    if "iqoo" in tokens:
        tokens.discard("vivo")
    return tokens


def sitemap_match_urls(phone: PhoneModel, product_urls: list[str]) -> list[str]:
    """Match a model name to sitemap slugs without accepting adjacent variants."""
    expected = _match_tokens(f"{phone.brand.name} {phone.model_name}")
    if not expected:
        return []
    model_has_capacity = bool(
        re.search(r"(?<![a-z0-9])\d+(?:gb|tb)(?![a-z0-9])", phone.model_name, re.IGNORECASE)
    )
    matches = []
    for url in product_urls:
        slug = url.rsplit("/", 1)[-1]
        if not model_has_capacity and re.search(
            r"(?<![a-z0-9])\d+(?:gb|tb)(?![a-z0-9])", slug, re.IGNORECASE
        ):
            continue
        actual = _match_tokens(slug)
        if actual == expected:
            matches.append(url)
    return sorted(matches, key=lambda value: (len(value), value))


def load_sitemap_urls(fetcher: SafeFetcher) -> list[str]:
    """Load the official sitemap, using a short local cache to avoid repeat traffic."""
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
        raise ValueError("MobileDokan sitemap 未返回有效产品页")
    SITEMAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SITEMAP_CACHE.write_text(json.dumps({
        "fetched_at": now.isoformat(),
        "source_url": SITEMAP_URL,
        "evidence": result.text_path.relative_to(PROJECT_ROOT).as_posix(),
        "urls": urls,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return urls


def parse_release(value: str) -> str | None:
    text = re.sub(r"\[[^]]+\]", "", value).strip()
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", text)
    if match and match.group(2).casefold() in MONTHS:
        return date(int(match.group(3)), MONTHS[match.group(2).casefold()], int(match.group(1))).isoformat()
    match = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2})", text)
    if match and match.group(1).casefold() in MONTHS:
        return date(int(match.group(3)), MONTHS[match.group(1).casefold()], int(match.group(2))).isoformat()
    return None


def first_number(value: str, unit: str | None = None) -> float | None:
    if unit == "g":
        pattern = r"(\d+(?:\.\d+)?)\s*(?:g|grams?)\b"
    else:
        pattern = rf"(\d+(?:\.\d+)?)\s*{unit}\b" if unit else r"(\d+(?:\.\d+)?)"
    match = re.search(pattern, value, re.IGNORECASE)
    return float(match.group(1)) if match else None


def boolean_value(value: str) -> bool | None:
    text = value.strip().casefold()
    if not text:
        return None
    if re.search(r"\bno\b|不支持|没有|无\b", text):
        return False
    if re.search(r"\byes\b|支持|有\b", text):
        return True
    return None


def table_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in soup.select("table.spec-grp-tbl"):
        current: dict[str, str] = {}
        for tr in table.select("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                label = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip().casefold()
                value = re.sub(r"\s+", " ", " ".join(cell.get_text(" ", strip=True) for cell in cells[1:])).strip()
                if label and value:
                    current[label] = value
        if current:
            rows.append(current)
    return rows


def extract_fields(html: str, final_url: str = "") -> tuple[dict[str, str], dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    tables = table_rows(soup)
    flat: dict[str, str] = {}
    for table in tables:
        flat.update(table)
    fields: dict[str, object] = {}
    source_labels: dict[str, str] = {}

    def set_field(field: str, value: object, label: str) -> None:
        if value is not None and value != "":
            fields[field] = value
            source_labels[field] = label

    if "release date" in flat:
        release = parse_release(flat["release date"])
        set_field("release_date", release, "Release Date")
    if not fields.get("release_date"):
        release_match = re.search(
            r"released[^.]{0,100}?(\d{1,2}\s+[A-Za-z]+\s+20\d{2}|[A-Za-z]+\s+\d{1,2},?\s+20\d{2})",
            page_text,
            re.IGNORECASE,
        )
        if release_match:
            set_field("release_date", parse_release(release_match.group(1)), "Release announcement")
    chipset = flat.get("chipset")
    if chipset:
        cpu = re.sub(r"\s+", " ", chipset).strip()[:160]
        set_field("cpu", cpu, "Chipset")
    if not fields.get("cpu"):
        cpu_match = re.search(
            r"\b((?:Snapdragon|Dimensity|Exynos|Kirin|Tensor|Unisoc)[A-Za-z0-9 +.-]{2,55})",
            page_text,
            re.IGNORECASE,
        )
        if cpu_match:
            set_field("cpu", cpu_match.group(1).strip(), "Chipset text")
    display = next((table for table in tables if "display type" in table or "screen size" in table), {})
    set_field("screen_type", display.get("display type"), "Display Type")
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*inch", display.get("screen size", ""), re.IGNORECASE)
    set_field("screen_size", float(size_match.group(1)) if size_match else None, "Screen Size")
    resolution = display.get("resolution", "")
    resolution_match = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", resolution, re.IGNORECASE)
    if resolution_match:
        set_field("resolution", f"{resolution_match.group(1)}x{resolution_match.group(2)}", "Resolution")
    set_field("refresh_rate", first_number(display.get("refresh rate", ""), "hz"), "Refresh Rate")
    display_text = " ".join(display.values()).casefold()
    if "fold" in display_text or "折叠" in display_text:
        set_field("screen_shape", "foldable", "Display shape")
    elif "曲面" in display_text or "curved" in display_text:
        set_field("screen_shape", "curved", "Display shape")
    elif "直屏" in display_text or "flat" in display_text:
        set_field("screen_shape", "flat", "Display shape")
    camera_tables = [table for table in tables if "camera setup" in table and "resolution" in table]
    if camera_tables:
        camera_text = camera_tables[0]["resolution"]
        camera = re.search(r"(\d+(?:\.\d+)?)\s*mp", camera_text, re.IGNORECASE)
        set_field("main_camera_mp", float(camera.group(1)) if camera else None, "Rear Camera Resolution")
        combined_camera = " ".join(camera_tables[0].values()).casefold()
        if any(token in combined_camera for token in ("telephoto", "periscope", "长焦", "潜望")):
            set_field("telephoto", True, "Telephoto camera")
    dimensions = next((table for table in tables if "thickness" in table or "weight" in table), {})
    set_field("weight_g", first_number(dimensions.get("weight", ""), "g"), "Weight")
    set_field("thickness_mm", first_number(dimensions.get("thickness", ""), "mm"), "Thickness")
    ip_match = re.search(r"IP(?:\d{2})(?:/IP\d{2})?(?:K)?", dimensions.get("ip rating", dimensions.get("waterproof", "")), re.IGNORECASE)
    if ip_match:
        set_field("waterproof", ip_match.group(0).upper(), "IP Rating")
    if not fields.get("waterproof"):
        ip_match = re.search(r"\bIP\d{2}(?:/IP\d{2})?K?\b", page_text, re.IGNORECASE)
        if ip_match:
            set_field("waterproof", ip_match.group(0).upper(), "Page IP text")
    if fields.get("waterproof"):
        set_field("waterproof_supported", True, "IP Rating")
    else:
        waterproof_flag = boolean_value(
            dimensions.get("waterproof", dimensions.get("water resistance", ""))
        )
        if waterproof_flag is not None:
            set_field("waterproof_supported", waterproof_flag, "Waterproof support")
    battery = next((table for table in tables if "capacity" in table and "battery type" in table), {})
    set_field("battery_mah", first_number(battery.get("capacity", ""), "mah"), "Battery Capacity")
    set_field("charging_w", first_number(battery.get("quick charging", ""), "w"), "Quick Charging")
    set_field("wireless_charging_w", first_number(battery.get("wireless charging", ""), "w"), "Wireless Charging")
    wireless_flag = boolean_value(battery.get("wireless charging", ""))
    if wireless_flag is not None:
        set_field("wireless_charging_supported", wireless_flag, "Wireless Charging")
    elif fields.get("wireless_charging_w"):
        set_field("wireless_charging_supported", True, "Wireless Charging")
    if not fields.get("wireless_charging_w"):
        wireless_match = re.search(r"(\d{2,3})\s*W\s*wireless", page_text, re.IGNORECASE)
        if wireless_match:
            set_field("wireless_charging_w", float(wireless_match.group(1)), "Wireless charging text")
    software = next((table for table in tables if "operating system" in table), {})
    os_parts = [software.get("operating system"), software.get("os version"), software.get("user interface")]
    os_text = ", ".join(part for part in os_parts if part)
    set_field("operating_system", os_text[:100] or None, "Operating System")
    nfc_flag = boolean_value(flat.get("nfc", ""))
    if nfc_flag is not None:
        set_field("nfc", nfc_flag, "NFC")
    five_g_flag = boolean_value(flat.get("5g", ""))
    if five_g_flag is None:
        network_text = " ".join(
            value for key, value in flat.items() if "network" in key
        )
        if re.search(r"(?<![a-z0-9])5g(?![a-z0-9])", network_text, re.IGNORECASE):
            five_g_flag = True
        elif network_text and re.search(r"(?<![a-z0-9])4g(?![a-z0-9])", network_text, re.IGNORECASE):
            five_g_flag = False
    if five_g_flag is not None:
        set_field("five_g", five_g_flag, "5G Network")
    image = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
    image_url = (image.get("content") or "").strip() if image else ""
    if image_url.startswith("https://"):
        set_field("image_url", image_url, "Social product image")
        if final_url:
            set_field("image_source_url", final_url, "Product page")
    return fields, source_labels


def page_identity(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ", strip=True)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return compact(f"{title} {text[:1500]}")


def crawl(*, limit: int | None = None, interval: float = 1.5, brand: str | None = None, report: Path | None = None) -> None:
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
        sitemap_urls = load_sitemap_urls(fetcher)
    except Exception as exc:
        print(f"警告：MobileDokan sitemap 不可用，回退到 URL 猜测：{exc}", flush=True)
        sitemap_urls = []
    records: list[dict] = []
    for index, phone in enumerate(targets, 1):
        record = {
            "phone_id": phone.id,
            "brand": phone.brand.name,
            "model": phone.model_name,
            "source": "https://www.mobiledokan.com/",
            "fields": {},
            "source_labels": {},
        }
        try:
            result = None
            chosen_url = ""
            match_source = ""
            candidates = [
                (url, "sitemap") for url in sitemap_match_urls(phone, sitemap_urls)
            ] + [
                (f"https://www.mobiledokan.com/mobile/{candidate}/", "slug")
                for candidate in slug_candidates(phone)
            ]
            for url, source in dict.fromkeys(candidates):
                try:
                    result = fetcher.fetch(url)
                    chosen_url = url
                    match_source = source
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
            if result is None:
                raise ValueError("未找到精确型号页")
            if match_source == "sitemap" and not sitemap_match_urls(
                phone, [str(result.url).rstrip("/")]
            ):
                raise ValueError("详情页跳转后的型号与 sitemap 目标不匹配")
            soup = BeautifulSoup(result.html, "html.parser")
            identity = page_identity(soup)
            expected = compact(f"{phone.brand.name} {phone.model_name}")
            if match_source == "slug" and expected not in identity and compact(phone.model_name) not in identity:
                raise ValueError("页面标题或规格表与机型不匹配")
            fields, labels = extract_fields(result.html, str(result.url))
            record.update({
                "url": str(result.url),
                "requested_url": chosen_url,
                "match_source": match_source,
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
            print(f"{index}/{len(targets)}: matched={sum(row['status'] == 'matched' for row in records)}", flush=True)
    output = report or PROJECT_ROOT / "data" / "reports" / f"third-party-spec-crawl-{datetime.now():%Y%m%d-%H%M%S}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "MobileDokan (third-party fallback)",
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
    backup = PROJECT_ROOT / "data" / "backups" / f"before-third-party-specs-{datetime.now():%Y%m%d-%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    database = Path(engine.url.database or "")
    with __import__("sqlite3").connect(database) as source, __import__("sqlite3").connect(backup) as target:
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
            verified, _ = extract_fields(evidence_html.read_text(encoding="utf-8"), row.get("url", ""))
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
                if field == "release_date":
                    value = date.fromisoformat(value)
                setattr(phone, field, value)
                counts[field] += 1
                changed = True
            if phone.main_camera_mp and not phone.camera_summary:
                phone.camera_summary = f"{phone.main_camera_mp:g} MP 主摄（第三方规格页核对）"
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
            fields, labels = extract_fields(evidence_html.read_text(encoding="utf-8"), row.get("url", ""))
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
    parser = argparse.ArgumentParser(description="第三方规格回退采集：只填空字段并保留来源报告")
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
        crawl(limit=args.limit, interval=args.interval, brand=args.brand, report=args.report)


if __name__ == "__main__":
    main()
