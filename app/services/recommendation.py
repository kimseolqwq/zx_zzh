from __future__ import annotations

import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Brand, ModelRun, PhoneModel, PhoneVariant, PlatformListing, RecommendationRun
from app.services.intent_parser import FOLDABLE_NEGATIVE_TOKENS, parse_intent
from app.services.ollama import ModelOpinion, OllamaClient
from app.services.pricing import (
    as_aware_utc,
    freshness_metadata,
    latest_snapshot,
    platform_search_urls,
)


@dataclass
class Requirements:
    budget: float
    usage: str
    brand: str | None
    min_storage_gb: int
    details: str
    min_budget: float = 0.0
    price_preference: str = "auto"
    intent: dict[str, Any] = field(default_factory=dict)


def _number(value: Decimal | float | int | None) -> float | None:
    return float(value) if value is not None else None


PLATFORM_NAMES = {"jd": "京东", "tmall": "天猫", "pdd": "拼多多"}
SCORE_WEIGHTS = {
    "requirement_match": ("需求匹配", 0.40),
    "model_consensus": ("模型共识", 0.15),
    "data_trust": ("数据可信", 0.20),
    "value": ("预算利用", 0.20),
    "freshness": ("时效性", 0.05),
}


def _latest_prices(variant: PhoneVariant) -> tuple[float | None, dict[str, Any] | None, list[dict[str, Any]]]:
    search_urls = platform_search_urls(
        variant.model.brand.name,
        variant.model.model_name,
        variant.variant_name,
    )
    manual_statuses = {"manual", "manual_unavailable", "manual_not_found"}
    rows: list[tuple[int, datetime, float, dict[str, Any]]] = []
    for listing in variant.listings:
        if not listing.is_active:
            continue
        latest = latest_snapshot(listing, require_in_stock=True, require_public_price=True)
        if latest is None:
            continue
        price = latest.public_sale_price or latest.regular_price
        if price is None:
            continue
        has_evidence = bool(latest.evidence_text_path or latest.screenshot_path)
        if listing.store_verified and latest.crawl_status == "reviewed" and has_evidence:
            trust_rank, trust_level, trust_label = 0, "verified", "已审核官方店"
        elif latest.crawl_status in manual_statuses:
            trust_rank, trust_level, trust_label = 1, "manual", "手工参考价"
        elif listing.store_verified:
            trust_rank, trust_level, trust_label = 2, "unreviewed", "待复核"
        else:
            continue
        rows.append(
            (
                trust_rank,
                latest.crawled_at,
                float(price),
                {
                    "platform": listing.platform,
                    "platform_name": PLATFORM_NAMES.get(listing.platform, listing.platform),
                    "store_name": listing.store_name,
                    "crawl_status": latest.crawl_status,
                    "is_reviewed": latest.crawl_status == "reviewed",
                    "has_evidence": has_evidence,
                    "trust_level": trust_level,
                    "trust_label": trust_label,
                    "store_verified": listing.store_verified,
                    **freshness_metadata(latest),
                    "url": listing.product_url,
                    "crawled_at": latest.crawled_at.isoformat(),
                },
            )
        )
    if not rows:
        empty = [
            {"platform": key, "platform_name": name, "price": None, "url": None, "purchase_url": search_urls[key], "link_verified": False, "is_lowest": False, "freshness_label": "尚未核验", "is_stale": True}
            for key, name in PLATFORM_NAMES.items()
        ]
        return _number(variant.launch_price), None, empty
    best_trust = min(row[0] for row in rows)
    lowest_row = min(
        (row for row in rows if row[0] == best_trust),
        key=lambda item: (item[2], -as_aware_utc(item[1]).timestamp()),
    )
    lowest_price, lowest_detail = lowest_row[2], lowest_row[3]
    by_platform: dict[str, tuple[int, datetime, float, dict[str, Any]]] = {}
    for row in rows:
        platform = row[3]["platform"]
        previous = by_platform.get(platform)
        if previous is None or (
            row[0],
            -as_aware_utc(row[1]).timestamp(),
        ) < (
            previous[0],
            -as_aware_utc(previous[1]).timestamp(),
        ):
            by_platform[platform] = row
    platform_prices: list[dict[str, Any]] = []
    for key, name in PLATFORM_NAMES.items():
        row = by_platform.get(key)
        if row is None:
            platform_prices.append({"platform": key, "platform_name": name, "price": None, "url": None, "purchase_url": search_urls[key], "link_verified": False, "is_lowest": False, "freshness_label": "尚未核验", "is_stale": True})
            continue
        detail = row[3]
        url = detail["url"] if detail["url"] and "example.com" not in detail["url"] else None
        platform_prices.append({
            **detail,
            "price": row[2],
            "url": url,
            "purchase_url": url or search_urls[key],
            "link_verified": bool(url),
            "is_lowest": row[2] == lowest_price and detail["platform"] == lowest_detail["platform"],
        })
    if lowest_detail:
        actual_url = lowest_detail.get("url")
        if actual_url and "example.com" in actual_url:
            actual_url = None
        lowest_detail = {
            **lowest_detail,
            "url": actual_url,
            "purchase_url": actual_url or search_urls[lowest_detail["platform"]],
            "link_verified": bool(actual_url),
        }
    return lowest_price, lowest_detail, platform_prices


def _candidate_from_variant(variant: PhoneVariant) -> dict[str, Any]:
    phone = variant.model
    price, price_detail, platform_prices = _latest_prices(variant)
    return {
        "model_id": phone.id,
        "variant_id": variant.id,
        "brand": phone.brand.name,
        "model": phone.model_name,
        "variant": variant.variant_name,
        "ram_gb": variant.ram_gb,
        "storage_gb": variant.storage_gb,
        "launch_price": _number(variant.launch_price),
        "current_price": price,
        "price_detail": price_detail,
        "platform_prices": platform_prices,
        "image_url": phone.image_url,
        "image_source_url": phone.image_source_url,
        # Reject parser placeholders/marketing prose before they reach either
        # the models or the user-facing explanation.
        "cpu": _usable_cpu(phone.cpu),
        "screen_size": phone.screen_size,
        "screen_type": phone.screen_type,
        "resolution": phone.resolution,
        "refresh_rate": phone.refresh_rate,
        "main_camera_mp": phone.main_camera_mp,
        "camera_summary": phone.camera_summary,
        "battery_mah": phone.battery_mah,
        "charging_w": phone.charging_w,
        "wireless_charging_w": phone.wireless_charging_w,
        "wireless_charging_supported": phone.wireless_charging_supported,
        "weight_g": phone.weight_g,
        "waterproof": phone.waterproof,
        "waterproof_supported": phone.waterproof_supported,
        "nfc": phone.nfc,
        "five_g": phone.five_g,
        "screen_shape": phone.screen_shape,
        "telephoto": phone.telephoto,
        "operating_system": phone.operating_system,
        "source_url": phone.source_url,
        "source_checked_at": phone.source_checked_at.isoformat() if phone.source_checked_at else None,
        "data_quality": phone.data_quality,
    }


def _is_foldable(candidate: dict[str, Any]) -> bool:
    text = f"{candidate.get('brand', '')} {candidate.get('model', '')}".casefold()
    return any(token in text for token in (
        "fold", "flip", "mate x", "find n", "magic v", "pura x", "razr",
        "mix fold", "折叠",
    ))


def _price_preference(details: str) -> str:
    text = (details or "").casefold()
    if any(token in text for token in ("越贵越好", "越贵越", "尽量贵", "上顶配", "用满预算", "接近预算上限", "接近上限", "价格越高越好")):
        return "high"
    if any(token in text for token in ("越便宜越好", "越便宜越", "低价优先", "省钱", "预算内便宜", "价格越低越好", "性价比优先")):
        return "low"
    return "balanced"


def _matches_special_requirements(candidate: dict[str, Any], details: str, intent: dict[str, Any] | None = None) -> bool:
    intent = intent or {}
    if candidate.get("brand") in intent.get("avoid_brands", []):
        return False
    preferred_brands = intent.get("preferred_brands") or []
    if preferred_brands and candidate.get("brand") not in preferred_brands:
        return False
    form_factor = intent.get("form_factor")
    if form_factor == "foldable" and not _is_foldable(candidate):
        return False
    if form_factor == "slab" and _is_foldable(candidate):
        return False
    must_have = set(intent.get("must_have") or [])
    if "wireless_charging" in must_have and not (
        candidate.get("wireless_charging_supported") is True
        or bool(candidate.get("wireless_charging_w"))
    ):
        return False
    if "waterproof" in must_have and not (
        candidate.get("waterproof_supported") is True
        or bool(candidate.get("waterproof"))
    ):
        return False
    if "telephoto" in must_have:
        camera_text = str(candidate.get("camera_summary") or "").casefold()
        if not (
            candidate.get("telephoto") is True
            or any(token in camera_text for token in ("长焦", "潜望", "tele", "periscope"))
        ):
            return False
    if "nfc" in must_have and candidate.get("nfc") is not True:
        return False
    if "five_g" in must_have and candidate.get("five_g") is not True:
        return False
    if "small_screen" in must_have:
        screen = candidate.get("screen_size")
        if screen is None or float(screen) > 6.4:
            return False
    if "lightweight" in must_have:
        weight = candidate.get("weight_g")
        if weight is None or float(weight) > 200:
            return False
    if "large_battery" in must_have:
        battery = candidate.get("battery_mah")
        if battery is None or float(battery) < 6000:
            return False
    if "high_refresh" in must_have:
        refresh = candidate.get("refresh_rate")
        if refresh is None or float(refresh) < 120:
            return False
    if "heavy" in set(intent.get("avoid") or []):
        weight = candidate.get("weight_g")
        if weight is not None and float(weight) > 210:
            return False
    if "curved_screen" in set(intent.get("avoid") or []):
        shape = str(candidate.get("screen_shape") or "").casefold()
        screen_type = str(candidate.get("screen_type") or "").casefold()
        if shape == "curved" or "曲面" in screen_type or "curved" in screen_type:
            return False
    text = (details or "").casefold()
    if not text:
        return True
    has_structured_intent = bool(intent)
    foldable = _is_foldable(candidate)
    avoids_foldable = any(token in text for token in FOLDABLE_NEGATIVE_TOKENS)
    wants_foldable = not avoids_foldable and any(token in text for token in ("折叠屏", "折叠手机", "foldable", " fold", "flip"))
    if wants_foldable and not foldable:
        return False
    if avoids_foldable and foldable:
        return False
    if not has_structured_intent and (
        "无线充" in text
        and "不需要无线" not in text
        and "不要无线" not in text
        and not (
            candidate.get("wireless_charging_supported") is True
            or bool(candidate.get("wireless_charging_w"))
        )
    ):
        return False
    if not has_structured_intent and (
        "防水" in text
        and "不需要防水" not in text
        and "不要防水" not in text
        and not (
            candidate.get("waterproof_supported") is True
            or bool(candidate.get("waterproof"))
        )
    ):
        return False
    if not has_structured_intent and any(token in text for token in ("长焦", "潜望", "望远")):
        camera_text = str(candidate.get("camera_summary") or "").casefold()
        if not (
            candidate.get("telephoto") is True
            or any(token in camera_text for token in ("长焦", "潜望", "tele", "periscope"))
        ):
            return False
    if any(token in text for token in ("小屏", "小尺寸")):
        screen = candidate.get("screen_size")
        if screen is None or float(screen) > 6.4:
            return False
    if any(token in text for token in ("轻薄", "轻一点", "重量轻")):
        weight = candidate.get("weight_g")
        if weight is None or float(weight) > 200:
            return False
    return True


def load_candidates(db: Session, requirements: Requirements, limit: int | None = 12) -> list[dict[str, Any]]:
    statement = (
        select(PhoneVariant)
        .join(PhoneVariant.model)
        .options(
            selectinload(PhoneVariant.model).selectinload(PhoneModel.brand),
            selectinload(PhoneVariant.listings).selectinload(PlatformListing.prices),
        )
        .where(PhoneVariant.is_active.is_(True), PhoneVariant.storage_gb >= requirements.min_storage_gb)
    )
    variants = db.scalars(statement).unique().all()
    candidates = [_candidate_from_variant(item) for item in variants if item.model.is_active and item.model.sale_status in {"on_sale", "partial_sale"}]
    if requirements.brand:
        candidates = [item for item in candidates if requirements.brand.lower() in item["brand"].lower()]
    candidates = [item for item in candidates if _matches_special_requirements(item, requirements.details, requirements.intent)]
    candidates.sort(key=lambda item: (item["current_price"] is None, item["current_price"] or math.inf))
    affordable = [
        item for item in candidates
        if item["current_price"] is not None
        and item["current_price"] <= requirements.budget
        and item["current_price"] >= requirements.min_budget
    ]
    return affordable if limit is None else affordable[:limit]


def _evidence_lines(
    candidate: dict[str, Any],
    requirements: Requirements,
    model_values: list[float],
    deviation: float,
) -> list[str]:
    price = candidate.get("current_price")
    lines = []
    if price is not None:
        margin = requirements.budget - price
        lines.append(f"公开参考价 ¥{price:,.0f}，比预算低 ¥{margin:,.0f}。")
    usage = requirements.usage
    if usage == "摄影创作" and candidate.get("camera_summary"):
        lines.append(f"影像依据：{candidate['camera_summary'][:55]}。")
    elif usage == "摄影创作" and candidate.get("main_camera_mp"):
        lines.append(f"数据库记录主摄 {candidate['main_camera_mp']:g} MP。")
    elif usage == "重度游戏":
        facts = []
        if candidate.get("refresh_rate"): facts.append(f"{candidate['refresh_rate']}Hz 屏幕")
        if candidate.get("battery_mah"): facts.append(f"{candidate['battery_mah']}mAh 电池")
        if candidate.get("charging_w"): facts.append(f"{candidate['charging_w']:g}W 充电")
        if facts: lines.append("游戏相关数据库参数：" + "、".join(facts) + "。")
    elif usage == "轻薄续航":
        facts = []
        if candidate.get("weight_g"): facts.append(f"{candidate['weight_g']:g}g")
        if candidate.get("battery_mah"): facts.append(f"{candidate['battery_mah']}mAh")
        if facts: lines.append("轻薄续航依据：" + "、".join(facts) + "。")
    else:
        facts = [str(value) for value in (candidate.get("cpu"), f"{candidate['battery_mah']}mAh" if candidate.get("battery_mah") else None) if value]
        if facts: lines.append("综合体验依据：" + "、".join(facts) + "。")
    if model_values:
        lines.append(f"{len(model_values)} 个模型标准化评分，平均 {statistics.mean(model_values):.1f}，分歧标准差 {deviation:.1f}。")
    else:
        lines.append("本次没有可解析模型评分，已使用规则分降级计算。")
    detail = candidate.get("price_detail")
    if detail:
        if detail.get("trust_level") == "manual":
            lines.append(f"最低价来自{detail['platform_name']}手工采集价（店铺/SKU未核验），{detail.get('freshness_label', '已记录录入时间')}。")
        elif detail.get("trust_level") == "verified":
            lines.append(f"最低价来自{detail['platform_name']}已审核官方店，{detail.get('freshness_label', '已记录采集时间')}。")
        else:
            lines.append(f"最低价来自{detail['platform_name']}待复核页面，仅作参考。")
    else:
        lines.append("暂无已核验平台价，当前价格使用公开发售价参考。")
    return lines[:4]


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _scale(value: float | int | None, low: float, high: float, missing: float = 55) -> float:
    if value is None:
        return missing
    return _clamp((float(value) - low) / (high - low) * 100)


def _usable_cpu(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split()).strip("，。,+ ")
    placeholders = {"CPU型号", "移动平台", "性能大爆发", "高性能处理器"}
    if cleaned in placeholders or len(cleaned) > 80 or "HarmonyOS" in cleaned:
        return None
    markers = ("骁龙", "天玑", "麒麟", "Exynos", "Tensor", "Snapdragon", "A18", "A19")
    return cleaned if any(marker.lower() in cleaned.lower() for marker in markers) else None


def _chipset_score(candidate: dict[str, Any]) -> float:
    cpu = (_usable_cpu(candidate.get("cpu")) or "").lower()
    model = candidate["model"].lower()
    if any(key in cpu for key in ("第五代骁龙8", "骁龙8至尊", "8至尊", "8 至尊", "a20", "a19 pro", "天玑9500", "麒麟9030")):
        score = 98
    elif any(key in cpu for key in ("第三代骁龙8", "第四代骁龙8", "a19", "a18", "天玑9400", "天玑9300", "麒麟9020")):
        score = 92
    elif any(key in cpu for key in ("骁龙8s", "骁龙8 gen", "天玑8500", "天玑8400", "麒麟9010", "unisoc t8", "exynos 2")):
        score = 82
    elif any(key in cpu for key in ("骁龙7", "天玑8", "天玑7", "麒麟8")):
        score = 70
    elif any(key in cpu for key in ("天玑6300", "骁龙6", "骁龙4", "unisoc t7")):
        score = 56
    else:
        score = 60
    if any(key in model for key in ("iqoo", "gt", "ace", "k80")):
        score = max(score, 84)
    if any(key in model for key in ("ultra", "pro max")):
        score = max(score, 90)
    return score


def _series_camera_score(candidate: dict[str, Any]) -> float:
    model = candidate["model"].lower()
    if "ultra" in model or "pura" in model:
        return 95
    if any(key in model for key in ("find x", "x200 pro", "x300 pro", "magic8 pro", "iphone 17 pro", "mate 70 pro", "mate 80 pro")):
        return 91
    if any(key in model for key in ("mate", "iphone", "x200", "x300", "s26", "s25", "find n")):
        return 84
    if any(key in model for key in ("reno", "civi", "nova", "s50", "s60")):
        return 76
    if "pro" in model:
        return 78
    return 65


def _camera_detail_score(candidate: dict[str, Any]) -> float:
    text = " ".join(str(candidate.get(key) or "") for key in ("camera_summary", "model", "cpu")).lower()
    score = 0.0
    for token, weight in (
        ("1英寸", 14), ("一英寸", 14), ("imx989", 12), ("lyt-900", 12),
        ("ois", 7), ("光学防抖", 7), ("潜望", 9), ("长焦", 7),
        ("徕卡", 5), ("蔡司", 5), ("哈苏", 5), ("大底", 6), ("传感器", 3),
    ):
        if token in text:
            score += weight
    mp = float(candidate.get("main_camera_mp") or 0)
    if mp >= 200:
        score += 12
    elif mp >= 108:
        score += 9
    elif mp >= 50:
        score += 6
    return _clamp(score)


def _display_score(candidate: dict[str, Any]) -> float:
    refresh = _scale(candidate.get("refresh_rate"), 60, 165, missing=45)
    screen_type = str(candidate.get("screen_type") or "").lower()
    panel = 92 if "ltpo" in screen_type else 82 if "amoled" in screen_type or "oled" in screen_type else 58 if screen_type else 55
    resolution = str(candidate.get("resolution") or "")
    match = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", resolution, re.IGNORECASE)
    pixels = int(match.group(1)) * int(match.group(2)) if match else 0
    pixel_score = 55 if not pixels else _clamp((pixels - 900_000) / (3_700_000 - 900_000) * 45 + 55)
    return _clamp(0.45 * refresh + 0.35 * panel + 0.20 * pixel_score)


def _feature_scores(candidate: dict[str, Any]) -> dict[str, float]:
    camera_mp = candidate.get("main_camera_mp")
    camera_sensor = 55 if camera_mp is None else _clamp(58 + math.sqrt(min(float(camera_mp), 200) / 50) * 22)
    camera = (
        0.48 * _series_camera_score(candidate)
        + 0.24 * camera_sensor
        + 0.20 * _camera_detail_score(candidate)
        + 0.08 * _scale(candidate.get("storage_gb"), 128, 1024)
    )
    display = _display_score(candidate)
    gaming = (
        0.43 * _chipset_score(candidate)
        + 0.25 * display
        + 0.16 * _scale(candidate.get("battery_mah"), 4000, 8000)
        + 0.10 * _scale(candidate.get("charging_w"), 18, 120)
        + 0.06 * _scale(candidate.get("ram_gb"), 6, 18)
    )
    weight_score = 55 if candidate.get("weight_g") is None else _clamp(100 - max(0, float(candidate["weight_g"]) - 170) * 1.35)
    endurance = (
        0.52 * _scale(candidate.get("battery_mah"), 4000, 8000)
        + 0.28 * weight_score
        + 0.14 * _scale(candidate.get("charging_w"), 18, 120)
        + 0.06 * _scale(candidate.get("wireless_charging_w"), 0, 50)
    )
    thickness = candidate.get("thickness_mm")
    thickness_score = 70 if thickness is None else _clamp(100 - max(0, float(thickness) - 7.5) * 18)
    portability = 0.72 * weight_score + 0.18 * thickness_score + 0.10 * _scale(candidate.get("screen_size"), 5.5, 7.2)
    return {
        "camera": _clamp(camera),
        "performance": _clamp(gaming),
        "display": _clamp(display),
        "battery": _clamp(endurance),
        "portability": _clamp(portability),
    }


def _preference_weights(usage: str, details: str, intent: dict[str, Any] | None = None) -> dict[str, float]:
    if intent and isinstance(intent.get("priority_weights"), dict):
        supplied = {key: float(intent["priority_weights"].get(key, 0) or 0) for key in ("camera", "performance", "display", "battery", "portability")}
        if sum(supplied.values()) > 0:
            total = sum(supplied.values())
            return {key: round(value / total, 4) for key, value in supplied.items()}
    weights = {
        "摄影创作": {"camera": 0.60, "performance": 0.13, "display": 0.11, "battery": 0.10, "portability": 0.06},
        "重度游戏": {"camera": 0.04, "performance": 0.40, "display": 0.25, "battery": 0.23, "portability": 0.08},
        "轻薄续航": {"camera": 0.05, "performance": 0.16, "display": 0.10, "battery": 0.35, "portability": 0.34},
        "综合体验": {"camera": 0.24, "performance": 0.24, "display": 0.20, "battery": 0.20, "portability": 0.12},
    }.get(usage, {"camera": 0.22, "performance": 0.22, "display": 0.18, "battery": 0.23, "portability": 0.15})
    text = (details or "").casefold()
    boosts = {
        "camera": ("拍照", "摄影", "影像", "相机", "长焦", "潜望", "人像", "视频"),
        "performance": ("性能", "游戏", "芯片", "处理器", "帧率", "电竞"),
        "display": ("屏幕", "显示", "高刷", "刷新率", "护眼", "分辨率"),
        "battery": ("续航", "电池", "充电", "快充"),
        "portability": ("轻薄", "重量", "手感", "小屏", "便携"),
    }
    for key, tokens in boosts.items():
        if any(token in text for token in tokens):
            weights[key] = weights.get(key, 0) + 0.18
    total = sum(weights.values()) or 1
    return {key: value / total for key, value in weights.items()}


def _preference_score(candidate: dict[str, Any], usage: str, details: str, intent: dict[str, Any] | None = None) -> float:
    features = _feature_scores(candidate)
    weights = _preference_weights(usage, details, intent)
    score = sum(features[key] * weight for key, weight in weights.items())
    soft = set((intent or {}).get("soft_requirements") or [])
    if "small_screen" in soft and candidate.get("screen_size") is not None and float(candidate["screen_size"]) <= 6.4:
        score += 3
    if "lightweight" in soft and candidate.get("weight_g") is not None and float(candidate["weight_g"]) <= 200:
        score += 3
    if "large_battery" in soft and candidate.get("battery_mah") is not None and float(candidate["battery_mah"]) >= 6000:
        score += 3
    if "high_refresh" in soft and candidate.get("refresh_rate") is not None and float(candidate["refresh_rate"]) >= 120:
        score += 3
    if "wireless_charging" in soft and (
        candidate.get("wireless_charging_supported") is True
        or bool(candidate.get("wireless_charging_w"))
    ):
        score += 3
    if "waterproof" in soft and (
        candidate.get("waterproof_supported") is True
        or bool(candidate.get("waterproof"))
    ):
        score += 3
    if "telephoto" in soft and candidate.get("telephoto") is True:
        score += 3
    if "nfc" in soft and candidate.get("nfc") is True:
        score += 2
    if "five_g" in soft and candidate.get("five_g") is True:
        score += 2
    return _clamp(score)


def _profile_scores(candidate: dict[str, Any]) -> dict[str, float]:
    features = _feature_scores(candidate)
    return {
        "摄影创作": _preference_score(candidate, "摄影创作", ""),
        "重度游戏": _preference_score(candidate, "重度游戏", ""),
        "轻薄续航": _preference_score(candidate, "轻薄续航", ""),
        "综合体验": _preference_score(candidate, "综合体验", ""),
    }


def _usage_score(candidate: dict[str, Any], usage: str) -> float:
    return _preference_score(candidate, usage, "")


def _base_components(candidate: dict[str, Any], requirements: Requirements, all_prices: list[float]) -> dict[str, float]:
    price = candidate.get("current_price") or requirements.budget * 1.5
    if requirements.min_budget <= price <= requirements.budget:
        budget_score = 100
    elif price > requirements.budget:
        budget_score = max(0, 100 - (price - requirements.budget) / requirements.budget * 160)
    else:
        budget_score = max(0, 100 - (requirements.min_budget - price) / max(requirements.min_budget, 1) * 120)
    brand_score = 100 if not requirements.brand or requirements.brand.lower() in candidate["brand"].lower() else 55
    usage_score = _preference_score(candidate, requirements.usage, requirements.details, requirements.intent)
    # Storage is already a hard constraint. Meeting it should not be scored as
    # zero or allow a larger but otherwise weaker variant to dominate.
    storage_score = 100.0
    release_date = candidate.get("release_date")
    release_score = 55.0
    if release_date:
        try:
            released = release_date if hasattr(release_date, "year") else datetime.fromisoformat(str(release_date))
            years = max(0.0, (datetime.now(timezone.utc).date() - released.date()).days / 365.25)
            release_score = _clamp(100 - max(0, years - 0.5) * 25)
        except (TypeError, ValueError):
            release_score = 55.0
    requirement = 0.79 * usage_score + 0.09 * budget_score + 0.05 * storage_score + 0.07 * release_score
    completeness_fields = ["cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "weight_g", "source_url"]
    completeness = sum(candidate.get(field) not in (None, "") for field in completeness_fields) / len(completeness_fields) * 100
    quality_bonus = {
        "official_verified": 10,
        "manual_official_review": 7,
        "official_partial": 2,
        "legacy_demo": -8,
        "demo": -8,
    }.get(candidate.get("data_quality"), 0)
    detail = candidate.get("price_detail")
    price_trust = 0
    if detail:
        if detail.get("crawl_status") == "reviewed" and detail.get("has_evidence"):
            price_trust = 8
        elif detail.get("crawl_status") in {"manual", "manual_unavailable", "manual_not_found"}:
            price_trust = -10
        else:
            price_trust = -4
    else:
        price_trust = -15
    data_trust = max(0, min(100, completeness + quality_bonus + price_trust))
    floor = requirements.min_budget if requirements.min_budget > 0 else requirements.budget * 0.45
    floor = min(floor, requirements.budget)
    span = max(1.0, requirements.budget - floor)
    utilization = _clamp((price - floor) / span, 0, 1)
    # Prefer using the available budget, but this remains a soft preference:
    # quality and data-trust components together carry much more weight.
    preference = requirements.price_preference if requirements.price_preference != "auto" else _price_preference(requirements.details)
    if preference == "high":
        value = 25 + 75 * utilization
    elif preference == "low":
        value = 100 - 60 * utilization
    else:
        value = 40 + 60 * utilization
    freshness = 35.0
    if detail and detail.get("crawled_at"):
        crawled = datetime.fromisoformat(detail["crawled_at"])
        if crawled.tzinfo is None:
            crawled = crawled.replace(tzinfo=timezone.utc)
        days = max(0, (datetime.now(timezone.utc) - crawled).days)
        freshness = max(0, 100 - days * 4)
    return {
        "requirement_match": round(requirement, 2),
        "data_trust": round(data_trust, 2),
        "value": round(value, 2),
        "freshness": round(freshness, 2),
    }


def _build_prompt(requirements: Requirements, candidates: list[dict[str, Any]]) -> tuple[str, str]:
    system_prompt = (
        "你是手机候选复核器。只能依据给出的字段评分，不得补充未知参数。"
        "只输出JSON，不解释思考过程。"
    )
    compact_candidates = [
        {
            "id": item["variant_id"], "name": f"{item['brand']} {item['model']} {item['variant']}",
            "price": item["current_price"], "cpu": _usable_cpu(item.get("cpu")),
            "hz": item.get("refresh_rate"), "camera_mp": item.get("main_camera_mp"),
            "battery": item.get("battery_mah"), "charge_w": item.get("charging_w"),
            "wireless_charge": item.get("wireless_charging_supported"),
            "waterproof": item.get("waterproof_supported"),
            "nfc": item.get("nfc"), "five_g": item.get("five_g"),
            "screen_shape": item.get("screen_shape"), "telephoto": item.get("telephoto"),
            "weight_g": item.get("weight_g"), "storage_gb": item.get("storage_gb"),
        }
        for item in candidates
    ]
    criteria = {
        "摄影创作": "重点比较相机、存储、性能和续航；像素不是画质的唯一依据，不得虚构传感器信息",
        "重度游戏": "重点比较芯片、刷新率、内存、电池和充电",
        "轻薄续航": "重点比较电池、重量和充电，同时兼顾基础性能",
        "综合体验": "均衡比较性能、相机、屏幕、续航和价格",
    }.get(requirements.usage, "均衡比较性能、相机、屏幕、续航和价格")
    user_prompt = f"""
需求：预算{requirements.min_budget:g}-{requirements.budget:g}元；用途{requirements.usage}；补充{requirements.details[:80] or '无'}
评分重点：{criteria}
候选手机：{json.dumps(compact_candidates, ensure_ascii=False)}
对全部{len(candidates)}个候选分别给0到100分，每个id必须且只能出现一次。严格输出一个以候选id为键的对象：{{"scores":{{"候选id":分数}}}}
""".strip()
    return system_prompt, user_prompt


def _model_scores(opinions: list[ModelOpinion]) -> dict[int, list[tuple[float, dict[str, Any]]]]:
    def score_number(value: Any) -> float:
        if isinstance(value, dict):
            for key in ("score", "scores", "overall", "total"):
                nested = value.get(key)
                if isinstance(nested, (int, float)) and not isinstance(nested, bool):
                    return float(nested)
            dimensions = [
                float(item)
                for key, item in value.items()
                if key not in {"id", "variant_id", "candidate_id"}
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
            ]
            if dimensions:
                return sum(dimensions) / len(dimensions)
            raise ValueError("nested score has no numeric dimensions")
        return float(value)

    result: dict[int, list[tuple[float, dict[str, Any]]]] = {}
    for opinion in opinions:
        payload = opinion.parsed or {}
        seen_in_opinion: set[int] = set()
        rankings = payload.get("rankings", [])
        if not rankings and isinstance(payload.get("scores"), dict):
            rankings = [
                {"variant_id": variant_id, "score": score}
                for variant_id, score in payload["scores"].items()
            ]
        elif not rankings and isinstance(payload.get("scores"), list):
            rankings = []
            for item in payload["scores"]:
                if isinstance(item, list) and len(item) >= 2:
                    rankings.append({"variant_id": item[0], "score": item[1]})
                elif isinstance(item, dict):
                    variant_value = item.get("variant_id", item.get("id"))
                    if variant_value is None:
                        variant_value = next(
                            (value for key, value in item.items() if key not in {"score", "scores"}),
                            None,
                        )
                    rankings.append(
                        {
                            "variant_id": variant_value,
                            "score": item.get("score", item.get("scores")),
                        }
                    )
        if not isinstance(rankings, list):
            continue
        for item in rankings:
            try:
                variant_id = int(item["variant_id"])
                score_value = item["score"]
                score = max(0.0, min(100.0, score_number(score_value)))
            except (KeyError, TypeError, ValueError):
                continue
            # A small model can repeat a candidate while completing JSON. One
            # model must never count as multiple independent votes.
            if variant_id in seen_in_opinion:
                continue
            seen_in_opinion.add(variant_id)
            result.setdefault(variant_id, []).append((score, item))
    return result


def _standardized_model_scores(
    opinions: list[ModelOpinion],
    expected_ids: set[int],
) -> dict[int, list[float]]:
    """Center each model's scores before averaging to reduce model-scale bias."""
    by_model: dict[str, dict[int, float]] = {}
    all_scores: list[float] = []
    for opinion in opinions:
        scores = {
            variant_id: values[0][0]
            for variant_id, values in _model_scores([opinion]).items()
            if variant_id in expected_ids and values
        }
        if scores:
            by_model[opinion.model_name] = scores
            all_scores.extend(scores.values())
    if not all_scores:
        return {}
    global_mean = statistics.mean(all_scores)
    adjusted: dict[int, list[float]] = {}
    for scores in by_model.values():
        model_mean = statistics.mean(scores.values())
        for variant_id, score in scores.items():
            adjusted.setdefault(variant_id, []).append(
                _clamp(score - model_mean + global_mean)
            )
    return adjusted


def _shortlist(candidates: list[dict[str, Any]], requirements: Requirements, limit: int = 6) -> list[dict[str, Any]]:
    prices = [item["current_price"] for item in candidates if item.get("current_price") is not None]
    ranked = []
    preference = requirements.price_preference if requirements.price_preference != "auto" else _price_preference(requirements.details)
    for item in candidates:
        components = _base_components(item, requirements, prices)
        if preference == "high":
            preliminary = 0.62 * components["requirement_match"] + 0.15 * components["data_trust"] + 0.23 * components["value"]
        elif preference == "low":
            preliminary = 0.68 * components["requirement_match"] + 0.16 * components["data_trust"] + 0.16 * components["value"]
        else:
            preliminary = 0.72 * components["requirement_match"] + 0.16 * components["data_trust"] + 0.12 * components["value"]
        ranked.append((preliminary, item))
    ranked.sort(key=lambda row: row[0], reverse=True)
    selected: list[dict[str, Any]] = []
    seen_models: set[int] = set()
    for _, item in ranked:
        if item["model_id"] in seen_models:
            continue
        selected.append(item)
        seen_models.add(item["model_id"])
        if len(selected) >= limit:
            break
    return selected


def _parallel_model_opinions(client: OllamaClient, installed: set[str], system_prompt: str, user_prompt: str) -> list[ModelOpinion]:
    configured = list(settings.ollama_models)
    available = [name for name in configured if name in installed]
    by_name: dict[str, ModelOpinion] = {
        name: ModelOpinion(name, False, 0, None, None, None, "", None, "模型未安装")
        for name in configured if name not in installed
    }
    if available:
        with ThreadPoolExecutor(max_workers=len(available), thread_name_prefix="ollama-fast") as pool:
            futures = {name: pool.submit(client.chat_json, name, system_prompt, user_prompt) for name in available}
            for name, future in futures.items():
                try:
                    by_name[name] = future.result()
                except Exception as exc:
                    by_name[name] = ModelOpinion(name, False, 0, None, None, None, "", None, str(exc))
    return [by_name[name] for name in configured]


def _repair_incomplete_opinions(
    client: OllamaClient,
    opinions: list[ModelOpinion],
    expected_ids: set[int],
    system_prompt: str,
    user_prompt: str,
) -> list[ModelOpinion]:
    """Retry a syntactically valid but incomplete small-model vote once."""
    repaired = list(opinions)
    ordered_ids = sorted(expected_ids)
    for index, opinion in enumerate(opinions):
        covered = set(_model_scores([opinion])) & expected_ids
        if not opinion.success or covered == expected_ids:
            continue
        repair_prompt = (
            f"{user_prompt}\n上一次漏掉或重复了ID。现在必须按这个顺序各输出一次：{ordered_ids}。"
            "scores必须是以ID为键、分数为值的JSON对象，不要输出其他ID或文字。"
        )
        retry = client.chat_json(opinion.model_name, system_prompt, repair_prompt)
        retry_covered = set(_model_scores([retry])) & expected_ids
        if len(retry_covered) > len(covered):
            retry.latency_ms += opinion.latency_ms
            retry.prompt_tokens = (opinion.prompt_tokens or 0) + (retry.prompt_tokens or 0)
            retry.response_tokens = (opinion.response_tokens or 0) + (retry.response_tokens or 0)
            repaired[index] = retry
    return repaired


def _natural_explanation(candidate: dict[str, Any], requirements: Requirements, rank: int, model_values: list[float], deviation: float) -> str:
    name = f"{candidate['model']} {candidate['variant']}"
    facts: list[str] = []
    cpu = _usable_cpu(candidate.get("cpu"))
    if requirements.usage == "摄影创作":
        if candidate.get("main_camera_mp") is not None:
            facts.append(f"数据库记录主摄 {candidate['main_camera_mp']:g} MP")
        facts.append(f"{candidate['storage_gb']}GB 存储适合保留照片和视频")
    elif requirements.usage == "重度游戏":
        if cpu:
            facts.append(f"搭载{cpu}")
        if candidate.get("refresh_rate"):
            facts.append(f"{candidate['refresh_rate']}Hz 屏幕有利于高帧体验")
        if candidate.get("battery_mah"):
            facts.append(f"{candidate['battery_mah']}mAh 电池更适合长时间使用")
    elif requirements.usage == "轻薄续航":
        if candidate.get("battery_mah"):
            facts.append(f"电池容量为 {candidate['battery_mah']}mAh")
        if candidate.get("weight_g"):
            facts.append(f"机身约 {candidate['weight_g']:g}g")
        if candidate.get("charging_w"):
            facts.append(f"支持 {candidate['charging_w']:g}W 充电")
    else:
        if cpu:
            facts.append(f"搭载{cpu}")
        if candidate.get("refresh_rate"):
            facts.append(f"配备 {candidate['refresh_rate']}Hz 屏幕")
        if candidate.get("battery_mah"):
            facts.append(f"电池容量为 {candidate['battery_mah']}mAh")
        if candidate.get("main_camera_mp"):
            facts.append(f"主摄为 {candidate['main_camera_mp']:g} MP")
    if not facts:
        facts.append(f"该版本提供 {candidate['storage_gb']}GB 存储并满足你的硬性条件")
    detail = candidate.get("price_detail")
    trust_level = detail.get("trust_level") if detail else None
    price_source = (
        "手工采集平台价（店铺/SKU未核验）"
        if trust_level == "manual"
        else "已审核官方店公开价"
        if trust_level == "verified"
        else "待复核平台参考价"
        if detail
        else "公开发售价参考"
    )
    margin = requirements.budget - float(candidate["current_price"])
    model_text = f"{len(model_values)} 个有效模型标准化均值 {statistics.mean(model_values):.1f}、分歧标准差 {deviation:.1f}" if model_values else "模型超时后已由规则评分安全降级"
    caution = "购买前仍需在结算页确认实时价格和补贴资格" if detail else "目前缺少已核验平台价，购买前请进入平台确认实时成交价"
    budget_text = f"预算区间 ¥{requirements.min_budget:,.0f}-¥{requirements.budget:,.0f}" if requirements.min_budget else f"预算不超过 ¥{requirements.budget:,.0f}"
    return f"第{rank}名推荐 {name}：" + "，".join(facts[:3]) + f"。{price_source}为 ¥{candidate['current_price']:,.0f}，{budget_text}，距离上限余 ¥{margin:,.0f}；{model_text}。{caution}。"


def recommend(db: Session, requirements: Requirements) -> dict[str, Any]:
    started = time.perf_counter()
    client = OllamaClient()
    known_brands = db.scalars(select(Brand.name).where(Brand.is_active.is_(True))).all()
    requirements.intent = parse_intent(requirements.details, requirements.usage, list(known_brands), client)
    intent_usage = requirements.intent.pop("_model_usage", {}) or {}
    if requirements.brand and requirements.brand not in requirements.intent["preferred_brands"]:
        requirements.intent["preferred_brands"].append(requirements.brand)
    if requirements.intent.get("min_budget") is not None:
        requirements.min_budget = float(requirements.intent["min_budget"])
    if requirements.intent.get("max_budget") is not None:
        requirements.budget = float(requirements.intent["max_budget"])
    if requirements.intent.get("price_preference") in {"high", "low"}:
        requirements.price_preference = requirements.intent["price_preference"]
    candidates = load_candidates(db, requirements, limit=None)
    candidates = _shortlist(candidates, requirements, limit=6)
    run = RecommendationRun(
        user_query=requirements.details or f"预算{requirements.min_budget}-{requirements.budget}元，{requirements.usage}",
        parsed_requirements=json.dumps(asdict(requirements), ensure_ascii=False),
        candidate_count=len(candidates),
        intent_model_name=intent_usage.get("model_name"),
        intent_latency_ms=intent_usage.get("latency_ms"),
        intent_prompt_tokens=intent_usage.get("prompt_tokens"),
        intent_response_tokens=intent_usage.get("response_tokens"),
        intent_tokens_per_second=intent_usage.get("tokens_per_second"),
        intent_success=intent_usage.get("success"),
        success=False,
        error_message="模型评审进行中",
    )
    db.add(run)
    # Release SQLite's write lock before the local models spend tens of seconds
    # generating. This keeps admin edits and other page requests responsive.
    db.commit()
    if not candidates:
        run.success = False
        run.error_message = "数据库中没有同时满足预算、品牌与存储条件的在售版本，请适当调整条件。"
        db.commit()
        return {"run_id": run.id, "results": [], "message": run.error_message}

    system_prompt, user_prompt = _build_prompt(requirements, candidates)
    installed = set(client.installed_models())
    opinions = _parallel_model_opinions(client, installed, system_prompt, user_prompt)
    opinions = _repair_incomplete_opinions(
        client,
        opinions,
        {candidate["variant_id"] for candidate in candidates},
        system_prompt,
        user_prompt,
    )

    for opinion in opinions:
        model_run = ModelRun(
            recommendation_id=run.id,
            model_name=opinion.model_name,
            latency_ms=opinion.latency_ms,
            prompt_tokens=opinion.prompt_tokens,
            response_tokens=opinion.response_tokens,
            tokens_per_second=opinion.tokens_per_second,
            success=opinion.success,
            json_parse_success=opinion.parsed is not None,
            response_text=opinion.response_text,
            parsed_response=json.dumps(opinion.parsed, ensure_ascii=False) if opinion.parsed else None,
            error_message=opinion.error,
        )
        db.add(model_run)

    scores_by_id = _model_scores(opinions)
    standardized_by_id = _standardized_model_scores(
        opinions, {candidate["variant_id"] for candidate in candidates}
    )
    prices = [item["current_price"] for item in candidates if item.get("current_price")]
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        components = _base_components(candidate, requirements, prices)
        model_items = scores_by_id.get(candidate["variant_id"], [])
        model_values = [item[0] for item in model_items]
        consensus_values = standardized_by_id.get(candidate["variant_id"], model_values)
        if consensus_values:
            average = statistics.mean(model_values) if model_values else statistics.mean(consensus_values)
            consensus_average = statistics.mean(consensus_values)
            deviation = statistics.pstdev(consensus_values) if len(consensus_values) > 1 else 12
            agreement = max(0.7, 1 - deviation / 100)
            raw_consensus = consensus_average * agreement
            consensus = _clamp(raw_consensus, components["requirement_match"] - 8, components["requirement_match"] + 8)
        else:
            average = consensus_average = components["requirement_match"]
            deviation, consensus = 25.0, components["requirement_match"] * 0.7
        components["model_consensus"] = round(consensus, 2)
        score_breakdown = [
            {
                "key": key,
                "label": label,
                "raw": components[key],
                "weight": int(weight * 100),
                "contribution": round(components[key] * weight, 2),
            }
            for key, (label, weight) in SCORE_WEIGHTS.items()
        ]
        final_score = sum(item["contribution"] for item in score_breakdown)
        pros = list(dict.fromkeys(pro for _, item in model_items for pro in item.get("pros", []) if isinstance(pro, str)))[:4]
        cons = list(dict.fromkeys(con for _, item in model_items for con in item.get("cons", []) if isinstance(con, str)))[:3]
        results.append(
            {
                **candidate,
                "score": round(final_score, 1),
                "components": components,
                "model_average": round(average, 1),
                "consensus_average": round(consensus_average, 1),
                "model_deviation": round(deviation, 1),
                "confidence": "高" if len(model_values) >= 3 and deviation <= 10 else "中" if model_values else "低",
                "reason": "",
                "pros": pros,
                "cons": cons,
                "model_votes": len(model_values),
                "score_breakdown": score_breakdown,
                "evidence_lines": _evidence_lines(candidate, requirements, consensus_values, deviation),
            }
        )
    # Budget proximity is only a tie-breaker among high-quality candidates.
    # A phone that is noticeably weaker should not win merely because its
    # price is closer to the user's upper budget.
    quality_index = {
        item["variant_id"]: (
            0.40 * item["components"]["requirement_match"]
            + 0.15 * item["components"]["model_consensus"]
            + 0.20 * item["components"]["data_trust"]
            + 0.05 * item["components"]["freshness"]
        )
        for item in results
    }
    best_quality = max(quality_index.values(), default=0)
    for item in results:
        gap = best_quality - quality_index[item["variant_id"]]
        factor = 1.0 if gap <= 7 else 0.55 if gap <= 14 else 0.25
        if factor < 1:
            item["components"]["value"] = round(item["components"]["value"] * factor, 2)
            for entry in item["score_breakdown"]:
                if entry["key"] == "value":
                    entry["contribution"] = round(item["components"]["value"] * entry["weight"] / 100, 2)
            item["score"] = round(sum(entry["contribution"] for entry in item["score_breakdown"]), 1)
    preference = requirements.price_preference if requirements.price_preference != "auto" else _price_preference(requirements.details)
    if preference in {"high", "low"}:
        eligible = [item for item in results if best_quality - quality_index[item["variant_id"]] <= 8]
        eligible.sort(key=lambda item: item["current_price"], reverse=preference == "high")
        selected = eligible[:3]
        if len(selected) < 3:
            selected_ids = {item["variant_id"] for item in selected}
            fallback = sorted(
                (item for item in results if item["variant_id"] not in selected_ids),
                key=lambda item: item["score"],
                reverse=True,
            )
            selected.extend(fallback[:3 - len(selected)])
        results = selected
    else:
        results.sort(key=lambda item: item["score"], reverse=True)
        results = results[:3]
    for index, item in enumerate(results, start=1):
        model_values = [score for score, _ in scores_by_id.get(item["variant_id"], [])]
        consensus_values = standardized_by_id.get(item["variant_id"], model_values)
        deviation = statistics.pstdev(consensus_values) if len(consensus_values) > 1 else (12 if consensus_values else 25)
        item["reason"] = _natural_explanation(item, requirements, index, consensus_values, deviation)
    run.total_latency_ms = (time.perf_counter() - started) * 1000
    run.final_result = json.dumps(results, ensure_ascii=False)
    run.success = True
    run.error_message = None
    db.commit()
    return {
        "run_id": run.id,
        "requirements": asdict(requirements),
        "results": results,
        "models": [
            {
                "name": item.model_name,
                "success": item.success,
                "json_parse_success": item.parsed is not None,
                "latency_ms": round(item.latency_ms, 1),
                "tokens_per_second": round(item.tokens_per_second, 2) if item.tokens_per_second else None,
                "error": item.error,
            }
            for item in opinions
        ],
        "total_latency_ms": round(run.total_latency_ms, 1),
        "formula": "需求匹配40% + 模型共识15% + 数据可信度20% + 预算利用20% + 时效性5%",
        "score_weights": [
            {"key": key, "label": label, "weight": int(weight * 100)}
            for key, (label, weight) in SCORE_WEIGHTS.items()
        ],
    }
