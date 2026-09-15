from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


SPEC_BOUNDS: dict[str, tuple[float, float]] = {
    "screen_size": (3.0, 8.0),
    "refresh_rate": (30.0, 240.0),
    "main_camera_mp": (5.0, 250.0),
    "battery_mah": (2000.0, 10000.0),
    "charging_w": (5.0, 300.0),
    "weight_g": (100.0, 400.0),
    "thickness_mm": (3.0, 20.0),
}

IMAGE_REJECT_TOKENS = (
    "logo", "favicon", "sprite", "avatar", "qrcode", "qr-code", "placeholder",
    "loading", "default-image", "/icon/", "icon-", "share-img", "/gnb/", "wechat", "weixin",
)


def is_likely_product_image_url(url: str) -> bool:
    parsed = urlparse(url)
    target = f"{parsed.path}?{parsed.query}".lower()
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not parsed.path.lower().endswith(".svg")
        and not any(token in target for token in IMAGE_REJECT_TOKENS)
    )


def _first_number(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return float(match.group(1))
    return None


def _first_text(patterns: list[str], text: str, max_length: int = 120) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()[:max_length]
    return None


def parse_official_specs(text: str) -> dict[str, Any]:
    """从官方规格页可见文本提取保守字段；缺失值保持为空，不猜测。"""
    normalized = re.sub(r"[\t\r \u00a0]+", " ", text)
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", normalized)
    result: dict[str, Any] = {
        "cpu": _first_text([
            r"(?:CPU\s*型号|处理器型号|移动平台|处理平台|芯片平台)\s*[:：]?\s*\n?([^\n]{2,80})",
            r"(?:处理器|芯片)\s*[:：]?\s*\n(?!\s*(?:CPU\s*型号|移动平台|处理平台|芯片平台))\s*([^\n]{2,80})",
        ], normalized),
        "screen_size": _first_number([
            r"(?:屏幕)?尺寸\s*[:：]?\s*\n?约?\s*(\d+(?:\.\d+)?)\s*英寸",
            r"尺寸（英寸）\s*[:：]?\s*\n?(\d+(?:\.\d+)?)\s*英寸",
            r"(\d+(?:\.\d+)?)\s*英寸\s*(?:OLED|AMOLED|LCD)",
            r"(\d+(?:\.\d+)?)\s*英寸[^\n]{0,45}(?:OLED|AMOLED|LCD|显示屏|全面屏)",
            r"(\d+(?:\.\d+)?)\s*[″”'’]{2}\s*\n\s*屏幕尺寸",
        ], normalized),
        "refresh_rate": _first_number([
            r"刷新率\s*[:：]?\s*\n?(?:最高支持|最高可达|最大支持)?\s*(\d{2,3})\s*Hz",
            r"(?:最高支持|刷新率|最高)?\s*(\d{2,3})\s*Hz\s*刷新率",
            r"(?:最高可达|最高支持)\s*(\d{2,3})\s*Hz",
            r"刷新率\s*[:：]?\s*(\d{2,3})\s*Hz",
            r"刷新率\s*[:：]?\s*(?:\d{1,2}\s*[-~至]\s*)?(\d{2,3})\s*Hz",
            r"(?:\d{1,2}\s*[-~至]\s*)?(\d{2,3})\s*Hz[^\n]{0,30}刷新率",
            r"(\d{2,3})\s*Hz\s*\n[^\n]{0,15}刷新率",
        ], normalized),
        "main_camera_mp": _first_number([
            r"后置摄像头像素\s*\n\s*(\d{3,5})\s*万[^\n]{0,45}(?:主摄|镜头)",
            r"(?:后置|主摄|主摄像头)[^\n]{0,50}?(\d{3,5})\s*万像素",
            r"(\d{3,5})\s*万像素[^\n]{0,30}主摄",
            r"后置摄像头(?:像素)?\s*\n(?:后置\s*\n)?(\d{3,5})\s*万像素",
            r"后置摄像头[^\n]{0,50}\n(\d{3,5})\s*万像素",
            r"后置\s*\n(\d{3,5})\s*万像素",
            r"(\d{3,5})\s*万像素的(?:广角摄像头|主摄)",
        ], normalized),
        "battery_mah": _first_number([
            r"等效\s*(\d{4,5})\s*mAh",
            r"(?:电池容量|典型容量)\s*[:：]?\s*\n?(\d{4,5})\s*mAh",
            r"(?:电池信息|电池)[^\n]{0,60}?典型容量\s*[:：]?\s*(\d{4,5})\s*mAh",
            r"电池容量(?:为)?\s*[:：]?\s*(\d{4,5})\s*毫安时",
            r"(\d{4,5})\s*mAh",
        ], normalized),
        "charging_w": _first_number([
            r"(?:充电规格|有线快充|快速充电|超级闪充|闪充|充电)[^\n]{0,40}?(\d{2,3})\s*W",
            r"(?:充电规格|有线快充|快速充电|超级闪充|闪充)\s*\n(?:[^\n]{0,50}\n){0,3}[^\n]{0,50}?(\d{2,3})\s*W",
        ], normalized),
        "weight_g": _first_number([
            r"重量\s*[:：]?\s*\n?约?\s*(\d{2,3}(?:\.\d+)?)\s*g",
            r"重量\s*[:：]?\s*\n?约?\s*(\d{2,3}(?:\.\d+)?)\s*克",
            r"重量\s*[:：]?\s*\n[^\n]{0,45}?约?\s*(\d{2,3}(?:\.\d+)?)\s*g",
            r"重量\s*[:：]?\s*\n[^\n]{0,45}?约?\s*(\d{2,3}(?:\.\d+)?)\s*克",
            r"重量(?:仅有|仅为|约为|为)?\s*(\d{2,3}(?:\.\d+)?)\s*g",
            r"重量(?:仅有|仅为|约为|为)?\s*(\d{2,3}(?:\.\d+)?)\s*克",
            r"约\s*(\d{2,3}(?:\.\d+)?)\s*克",
        ], normalized),
        "thickness_mm": _first_number([
            r"厚度\s*[:：]?\s*\n?约?\s*(\d+(?:\.\d+)?)\s*mm",
            r"厚度\s*[:：]?\s*\n[^\n]{0,45}?约?\s*(\d+(?:\.\d+)?)\s*mm",
        ], normalized),
        "resolution": _first_text([
            r"分辨率\s*[:：]?\s*\n?([0-9]{3,4}\s*[×xX]\s*[0-9]{3,4}(?:\s*像素)?)",
            r"([0-9]{3,4}\s*[×xX]\s*[0-9]{3,4}\s*像素)\s*分辨率",
        ], normalized),
        "operating_system": _first_text([
            r"(?:操作系统|系统)\s*[:：]?\s*\n?((?:HarmonyOS|Android|iOS|ColorOS|OriginOS|HyperOS)[^\n]{0,40})",
            r"\b((?:iOS|ColorOS|OriginOS|HyperOS)\s*\d{1,2}(?:\.\d+)?)\b",
        ], normalized),
    }
    if result["main_camera_mp"]:
        result["main_camera_mp"] = result["main_camera_mp"] / 100
    else:
        mp_camera = re.search(
            r"(\d{1,3}(?:\.\d+)?)\s*MP[^\n]{0,35}(?:主摄|主摄像头)",
            normalized,
            flags=re.IGNORECASE,
        )
        if mp_camera:
            result["main_camera_mp"] = float(mp_camera.group(1))
        hundred_mp = re.search(
            r"(?:后置(?:摄像头)?像素|主摄)\s*[:：]?\s*\n?[^\n]{0,25}?(\d(?:\.\d+)?)\s*亿像素",
            normalized,
        )
        if hundred_mp and result["main_camera_mp"] is None:
            result["main_camera_mp"] = float(hundred_mp.group(1)) * 100
    # Reject physically implausible matches caused by nearby marketing text,
    # footnote numbers, touch-sampling rates, or auxiliary sensors.  Keeping a
    # field empty is safer than allowing an incorrect value into ranking.
    for field_name, (minimum, maximum) in SPEC_BOUNDS.items():
        value = result.get(field_name)
        if value is not None and not minimum <= float(value) <= maximum:
            result[field_name] = None
    return result


def completeness(specs: dict[str, Any]) -> float:
    important = ["cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "weight_g"]
    return round(sum(specs.get(key) is not None for key in important) / len(important) * 100, 1)


def extract_official_image(html: str, page_url: str, expected_name: str | None = None) -> dict[str, str] | None:
    """Read only explicit social/primary image metadata from the official page.

    We deliberately avoid guessing from arbitrary content images because those
    are often camera samples, people, accessories, or AI illustrations.
    """
    soup = BeautifulSoup(html, "html.parser")
    for selector in (
        'meta[property="og:image"]',
        'meta[property="og:image:secure_url"]',
        'meta[name="twitter:image"]',
    ):
        tag = soup.select_one(selector)
        content = (tag.get("content") or "").strip() if tag else ""
        image_url = urljoin(page_url, content)
        if content and not content.startswith("data:") and is_likely_product_image_url(image_url):
            return {"image_url": image_url, "image_source_url": page_url}
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            import json

            payload = json.loads(tag.string or "null")
        except (TypeError, ValueError):
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                image = current.get("image")
                image_url = urljoin(page_url, image) if isinstance(image, str) else ""
                if isinstance(image, str) and image and not image.startswith("data:") and is_likely_product_image_url(image_url):
                    return {"image_url": image_url, "image_source_url": page_url}
                if isinstance(image, list):
                    stack.extend(image)
                elif isinstance(image, dict):
                    stack.append(image)
                stack.extend(value for key, value in current.items() if key != "image" and isinstance(value, (dict, list)))
            elif isinstance(current, list):
                stack.extend(current)
    if expected_name:
        compact_name = re.sub(r"[\s\-+]+", "", expected_name).casefold()
        name_tokens = [token for token in re.split(r"[\s\-+]+", expected_name.casefold()) if len(token) >= 3]
        for image in soup.select("img[alt]"):
            alt = re.sub(r"[\s\-+]+", "", image.get("alt", "")).casefold()
            if compact_name not in alt and not any(token in alt for token in name_tokens[-2:]):
                continue
            content = next((image.get(attr) for attr in ("src", "data-src", "data-original") if image.get(attr)), "")
            image_url = urljoin(page_url, content)
            if content and not content.startswith(("data:", "blob:")) and is_likely_product_image_url(image_url):
                return {"image_url": image_url, "image_source_url": page_url}
    return None


def parse_release_date(text: str) -> date | None:
    """Parse only dates explicitly labelled as launch/release dates."""
    normalized = re.sub(r"[\t\r ]+", " ", text)
    patterns = (
        r"(?:上市时间|发布时间|正式发布)\s*[:：]?\s*(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?",
        r"(?:上市时间|发布时间|正式发布)\s*[:：]?\s*(20\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            day = int(match.group(3) or 1)
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def parse_memory_variants(text: str) -> list[dict[str, int | Decimal | None | str]]:
    """Extract explicit RAM/storage combinations and nearby suggested prices.

    Prefer explicit RAM/storage combinations.  Some manufacturers (notably
    Huawei and Apple) publish RAM and storage as separate lists; in that case
    we conservatively create storage-only variants instead of inventing a
    Cartesian product of RAM and storage values.
    """
    normalized = re.sub(r"\s+", " ", text).replace("＋", "+")
    pattern = re.compile(
        r"(?P<ram>4|6|8|12|16|18|24|32)\s*GB\s*\+\s*"
        r"(?P<storage>64|128|256|512|1024|2048|1\s*TB|2\s*TB)"
        r"\s*(?P<unit>GB|TB)?",
        flags=re.IGNORECASE,
    )
    found: dict[tuple[int, int], dict[str, int | Decimal | None | str]] = {}
    for match in pattern.finditer(normalized):
        ram = int(match.group("ram"))
        raw_storage = re.sub(r"\s+", "", match.group("storage").upper())
        if raw_storage.endswith("TB"):
            storage = int(raw_storage[:-2]) * 1024
        else:
            storage = int(raw_storage)
            if (match.group("unit") or "").upper() == "TB":
                storage *= 1024
        nearby = normalized[match.end():match.end() + 55]
        price_match = re.search(r"^[^\d]{0,12}(\d{3,5})(?:\.00)?\s*元", nearby)
        price = Decimal(price_match.group(1)) if price_match else None
        found[(ram, storage)] = {
            "ram_gb": ram,
            "storage_gb": storage,
            "variant_name": f"{ram}GB+{storage if storage < 1024 else str(storage // 1024) + 'TB'}",
            "launch_price": price,
        }
    shared_ram_pattern = re.compile(
        r"(?P<ram>4|6|8|12|16|18|24|32)\s*GB\s*RAM\s*\+\s*"
        r"(?P<storages>(?:64|128|256|512|1024|2048|1\s*TB|2\s*TB)\s*(?:GB|TB)?"
        r"(?:\s*/\s*(?:64|128|256|512|1024|2048|1\s*TB|2\s*TB)\s*(?:GB|TB)?)+)\s*ROM",
        flags=re.IGNORECASE,
    )
    for match in shared_ram_pattern.finditer(normalized):
        ram = int(match.group("ram"))
        for value in re.finditer(r"(64|128|256|512|1024|2048|1\s*TB|2\s*TB)\s*(GB|TB)?", match.group("storages"), re.IGNORECASE):
            raw_value = re.sub(r"\s+", "", value.group(1).upper())
            storage = int(raw_value[:-2]) * 1024 if raw_value.endswith("TB") else int(raw_value)
            if not raw_value.endswith("TB") and (value.group(2) or "").upper() == "TB":
                storage *= 1024
            found[(ram, storage)] = {
                "ram_gb": ram,
                "storage_gb": storage,
                "variant_name": f"{ram}GB+{storage if storage < 1024 else str(storage // 1024) + 'TB'}",
                "launch_price": None,
            }
    if found:
        return list(found.values())

    # Accept storage-only values only inside a clearly labelled ROM/storage
    # fragment.  Requiring both a storage label and GB/TB units prevents screen
    # memory, cache and marketing numbers elsewhere on the page becoming SKUs.
    storage_fragments: list[str] = []
    labelled_patterns = (
        r"(?:机身内存(?:（?ROM）?)?|存储容量|存储空间|容量版本)\s*[:：]?\s*([^\n]{1,100})",
        r"([^\n]{1,100}\bROM\b)",
    )
    line_text = text.replace("\r", "")
    for labelled_pattern in labelled_patterns:
        storage_fragments.extend(
            match.group(1) for match in re.finditer(labelled_pattern, line_text, flags=re.IGNORECASE)
        )
    storage_found: dict[int, dict[str, int | Decimal | None | str]] = {}
    value_pattern = re.compile(r"(?<!\d)(64|128|256|512|1024|2048|1\s*TB|2\s*TB)\s*(GB|TB)?", re.IGNORECASE)
    for fragment in storage_fragments:
        for match in value_pattern.finditer(fragment):
            raw_value = re.sub(r"\s+", "", match.group(1).upper())
            if raw_value.endswith("TB"):
                storage = int(raw_value[:-2]) * 1024
            else:
                storage = int(raw_value)
                if (match.group(2) or "").upper() == "TB":
                    storage *= 1024
            storage_found[storage] = {
                "ram_gb": None,
                "storage_gb": storage,
                "variant_name": f"{storage if storage < 1024 else str(storage // 1024) + 'TB'}",
                "launch_price": None,
            }
    return list(storage_found.values())
