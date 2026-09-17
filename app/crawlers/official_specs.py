from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


SPEC_BOUNDS: dict[str, tuple[float, float]] = {
    "screen_size": (3.0, 12.0),
    "refresh_rate": (30.0, 240.0),
    "main_camera_mp": (5.0, 250.0),
    "battery_mah": (2000.0, 12000.0),
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
            value = re.sub(r"\s+", " ", match.group(1)).strip()[:max_length]
            return value or None
    return None


def extract_official_metadata(html: str) -> str:
    """Collect explicit page metadata without treating navigation as specs."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    values: list[str] = []
    wanted = {
        "description", "keywords", "og:title", "og:description",
        "twitter:title", "twitter:description",
    }
    for tag in soup.find_all("meta"):
        key = (tag.get("name") or tag.get("property") or tag.get("http-equiv") or "").strip().casefold()
        content = (tag.get("content") or "").strip()
        if key in wanted and content:
            values.append(content)
    if soup.title:
        title = soup.title.get_text(" ", strip=True)
        if title:
            values.append(title)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(tag.string or "null")
        except (TypeError, ValueError):
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key in ("name", "headline", "description", "datePublished", "releaseDate"):
                    value = current.get(key)
                    if isinstance(value, str) and value.strip():
                        values.append(value.strip())
                stack.extend(value for value in current.values() if isinstance(value, (dict, list)))
            elif isinstance(current, list):
                stack.extend(current)
    return "\n".join(dict.fromkeys(values))


def parse_official_specs(text: str) -> dict[str, Any]:
    """从官方规格页可见文本提取保守字段；缺失值保持为空，不猜测。"""
    normalized = re.sub(r"[\t\r \u00a0]+", " ", text)
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", normalized)
    result: dict[str, Any] = {
        "cpu": _first_text([
            r"(?:^|[\n;；。|])\s*(?:处理器|芯片)\s*[:：]?\s*\n\s*(?:CPU\s*型号|处理器型号|移动平台|处理平台|芯片平台)\s*[:：]?\s*\n?\s*([^\n]{2,80})",
            r"(?:^|[\n;；。|])\s*(?:CPU\s*型号|处理器型号|移动平台|处理平台|芯片平台|处理器|芯片)\s*[:：]?\s*([^\n]{2,80})",
            r"(?:搭载|采用|配备)(?:新一代\s*)?((?:第[一二三四五六七八九十]+代\s*)?(?:骁龙|天玑|Exynos|麒麟)[^\n，。；]{2,40}?(?:移动平台|芯片|处理器))",
            r"\b(A\d{2}(?:\s*(?:Pro|Max))?\s*芯片)\b",
        ], normalized),
        "screen_size": _first_number([
            r"(?:屏幕)?尺寸\s*[:：]?\s*\n?约?\s*(\d+(?:\.\d+)?)\s*英寸",
            r"尺寸（英寸）\s*[:：]?\s*\n?(\d+(?:\.\d+)?)\s*英寸",
            r"(\d+(?:\.\d+)?)\s*英寸\s*(?:OLED|AMOLED|LCD)",
            r"(\d+(?:\.\d+)?)\s*英寸[^\n]{0,45}(?:OLED|AMOLED|LCD|显示屏|全面屏)",
            r"(\d+(?:\.\d+)?)\s*[″”'’]{2}\s*\n\s*屏幕尺寸",
            r"(\d+(?:\.\d+)?)\s*[″”]\s*(?:质感|小尺寸|低功耗|屏幕|显示屏|OLED|AMOLED)",
            r"显示屏[^\n]{0,100}?对角线长度(?:约为|是)\s*(\d+(?:\.\d+)?)\s*英寸",
        ], normalized),
        "screen_type": _first_text([
            r"(?:屏幕类型|屏幕材质|显示屏类型|面板类型)\s*[:：]?\s*\n?\s*(LTPO\s*OLED|AMOLED|OLED|LCD)",
            r"屏幕[^\n]{0,20}?(?:类型|材质)\s*[:：]?\s*(LTPO\s*OLED|AMOLED|OLED|LCD)",
            r"(?:屏幕|显示屏)\s*[:：]?\s*\n(?:[^\n]{0,60}\n){0,3}[^\n]{0,50}?\b(LTPO\s*OLED|AMOLED|OLED|LCD)\b",
            r"\b(LTPO\s*OLED|AMOLED|OLED|LCD)\s*(?:全面屏|显示屏|屏幕)",
        ], normalized),
        "refresh_rate": _first_number([
            r"(?:最高支持|刷新率|最高)?\s*(\d{2,3})\s*Hz\s*刷新率",
            r"(?:最高可达|最高支持)\s*(\d{2,3})\s*Hz",
            r"刷新率\s*[:：]?\s*\n?(?:最高支持|最高可达|最大支持)?\s*(\d{2,3})\s*Hz",
            r"刷新率\s*[:：]?\s*(?:最高|最高支持|最高可达|最大支持)\s*(\d{2,3})\s*Hz",
            r"刷新率\s*[:：]?\s*(\d{2,3})\s*Hz",
            r"刷新率\s*[:：]?\s*(?:\d{1,2}\s*[-~至]\s*)?(\d{2,3})\s*Hz",
            r"(?:\d{1,2}\s*[-~至]\s*)?(\d{2,3})\s*Hz[^\n]{0,30}刷新率",
            r"(\d{2,3})\s*Hz\s*\n[^\n]{0,15}刷新率",
            r"(\d{2,3})\s*Hz\s*(?:超感高刷屏|护眼电竞屏|电竞屏|高刷屏)",
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
            r"(?:充电规格|有线快充|快速充电|超级闪充|闪充|充电)[^\n]{0,40}?(\d{2,3})\s*W(?!\s*(?:超级)?(?:闪充|快充)?充电器)",
            r"(?:充电规格|有线快充|快速充电|超级闪充|闪充)\s*\n(?:[^\n]{0,50}\n){0,3}[^\n]{0,50}?(\d{2,3})\s*W(?!\s*(?:超级)?(?:闪充|快充)?充电器)",
            r"(\d{2,3})\s*W[^\n，。；]{0,24}(?:有线)?(?:超级)?(?:秒充|闪充|快充)(?!充电器|套装)",
            r"(?:支持|最大)\s*(\d{2,3})\s*W[^\n，。；]{0,24}(?:有线)?(?:超级)?(?:秒充|闪充|快充)(?!充电器|套装)",
        ], normalized),
        "weight_g": _first_number([
            r"重量\s*[:：]?\s*\n?约?\s*(\d{2,3}(?:\.\d+)?)\s*g",
            r"重量\s*[:：]?\s*\n?约?\s*(\d{2,3}(?:\.\d+)?)\s*克",
            r"重量\s*[:：]?\s*\n[^\n]{0,45}?约?\s*(\d{2,3}(?:\.\d+)?)\s*g",
            r"重量\s*[:：]?\s*\n[^\n]{0,45}?约?\s*(\d{2,3}(?:\.\d+)?)\s*克",
            r"重量(?:仅有|仅为|约为|为)?\s*[=:：≈]?\s*(\d{2,3}(?:\.\d+)?)\s*g",
            r"重量(?:仅有|仅为|约为|为)?\s*[=:：≈]?\s*(\d{2,3}(?:\.\d+)?)\s*克",
            r"约\s*(\d{2,3}(?:\.\d+)?)\s*克",
        ], normalized),
        "thickness_mm": _first_number([
            r"(?:厚度|厚)\s*[:：]?\s*\n?约?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)",
            r"(?:厚度|厚)\s*[:：]?\s*\n[^\n]{0,45}?约?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)",
        ], normalized),
        "resolution": _first_text([
            r"分辨率\s*[:：]?\s*\n?(?:[A-Z0-9+.\- ]{0,16})?(?<!\d)([0-9]{3,4}\s*[×xX]\s*[0-9]{3,4}(?:\s*像素)?)",
            r"(?<!\d)([0-9]{3,4}\s*[×xX]\s*[0-9]{3,4}\s*像素)\s*分辨率",
        ], normalized),
        "wireless_charging_w": _first_number([
            r"(\d{2,3})\s*W[^\n，。；]{0,20}无线(?:超级)?(?:闪充|快充|充电)(?!器|套装)",
            r"无线(?:超级)?(?:闪充|快充|充电)[^\n，。；]{0,20}(\d{2,3})\s*W(?!\s*(?:充电器|套装))",
        ], normalized),
        "operating_system": _first_text([
            r"\b((?:iOS|ColorOS|OriginOS|MagicOS|HyperOS)\s*\d{1,2}(?:\.\d+)?)\b",
            r"((?:小米)?澎湃OS\s*\d{1,2}(?:\.\d+)?)",
            r"(HarmonyOS\s*\d{1,2}(?:\.\d+)?)",
            r"(?:操作系统|系统)\s*[:：]?\s*\n?((?:HarmonyOS|Android|iOS|ColorOS|OriginOS|MagicOS|HyperOS|澎湃OS)[^\n]{0,40})",
        ], normalized),
        "waterproof": _first_text([
            r"\b(IP(?:68|69|69K|67|66|65|64)(?:/IP(?:68|69|69K|67|66|65|64))?)\b",
            r"((?:IP(?:68|69|69K|67|66|65|64)(?:/IP(?:68|69|69K|67|66|65|64))?)\s*(?:级)?(?:防尘防水|防水防尘|防水))",
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
        if not mp_camera:
            mp_camera = re.search(
                r"(?:主摄|主摄像头|广角摄像头)[^\n]{0,35}?(\d{1,3}(?:\.\d+)?)\s*MP",
                normalized,
                flags=re.IGNORECASE,
            )
        if mp_camera:
            result["main_camera_mp"] = float(mp_camera.group(1))
        m_camera = re.search(
            r"(\d{1,3}(?:\.\d+)?)\s*M\s*主摄",
            normalized,
            flags=re.IGNORECASE,
        )
        if m_camera and result["main_camera_mp"] is None:
            result["main_camera_mp"] = float(m_camera.group(1))
        hundred_mp = re.search(
            r"(?:后置(?:摄像头)?像素|后置摄像头|后置|主摄)\s*[:：]?[^\n]{0,25}?(\d(?:\.\d+)?)\s*亿像素",
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
    if result.get("waterproof"):
        match = re.search(r"IP(?:68|69|69K|67|66|65|64)(?:/IP(?:68|69|69K|67|66|65|64))?", result["waterproof"], re.IGNORECASE)
        result["waterproof"] = match.group(0).upper() if match else None
    if result.get("cpu"):
        result["cpu"] = result["cpu"].replace("\ufe0f", "")
        result["cpu"] = re.sub(r"^[\s|]+", "", result["cpu"])
        result["cpu"] = re.sub(r"^(?:CPU\s*型号|处理器型号|移动平台|处理平台|芯片平台|处理器|芯片|型号)\s*[:：]?\s*", "", result["cpu"]).strip()
        result["cpu"] = result["cpu"].split("|", 1)[0].split("，", 1)[0].strip()
        if (
            re.fullmatch(r"(?:\d+核|八核|四核|六核|双核).*处理器", result["cpu"])
            or re.search(r"(?:工艺|制程|主频|核心)", result["cpu"])
            or re.search(r"[^\u4e00-\u9fffA-Za-z0-9®™+.\s-]", result["cpu"])
            or not re.search(
                r"(?:骁龙|天玑|Exynos|麒麟|Helio|Tensor|Snapdragon|Dimensity|Apple\s*[AM]\d+|\b[AM]\d{1,2}(?:\s*(?:Pro|Max))?\b)",
                result["cpu"],
                flags=re.IGNORECASE,
            )
        ):
            result["cpu"] = None
    if re.search(r"(?:不支持|无|没有)\s*NFC", normalized, re.IGNORECASE):
        result["nfc"] = False
    elif re.search(r"(?:支持|具备|配备|采用)\s*NFC|NFC\s*(?:功能|支持)", normalized, re.IGNORECASE):
        result["nfc"] = True
    else:
        result["nfc"] = None
    if re.search(r"(?:不支持|无|没有)\s*5G", normalized, re.IGNORECASE):
        result["five_g"] = False
    elif re.search(r"(?:支持|具备|适用)\s*5G|5G\s*(?:网络|手机)|双卡双5G", normalized, re.IGNORECASE):
        result["five_g"] = True
    else:
        result["five_g"] = None
    if re.search(r"折叠", normalized):
        result["screen_shape"] = "foldable"
    elif re.search(r"曲面屏|曲面", normalized):
        result["screen_shape"] = "curved"
    elif re.search(r"直屏|平面屏", normalized):
        result["screen_shape"] = "flat"
    else:
        result["screen_shape"] = None
    result["telephoto"] = bool(
        re.search(r"长焦|潜望|telephoto|periscope", normalized, re.IGNORECASE)
    ) or None
    if result.get("wireless_charging_w"):
        result["wireless_charging_supported"] = True
    elif re.search(r"(?:不支持|无|没有)\s*无线充", normalized, re.IGNORECASE):
        result["wireless_charging_supported"] = False
    else:
        result["wireless_charging_supported"] = None
    if result.get("waterproof"):
        result["waterproof_supported"] = True
    elif re.search(r"(?:不支持|无|没有)\s*防水", normalized, re.IGNORECASE):
        result["waterproof_supported"] = False
    else:
        result["waterproof_supported"] = None
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
