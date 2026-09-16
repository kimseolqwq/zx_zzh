from __future__ import annotations

import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import ModelRun, PhoneModel, PhoneVariant, PlatformListing, RecommendationRun
from app.services.ollama import ModelOpinion, OllamaClient
from app.services.pricing import freshness_metadata, latest_snapshot


@dataclass
class Requirements:
    budget: float
    usage: str
    brand: str | None
    min_storage_gb: int
    details: str


def _number(value: Decimal | float | int | None) -> float | None:
    return float(value) if value is not None else None


PLATFORM_NAMES = {"jd": "京东", "tmall": "天猫", "pdd": "拼多多"}
SCORE_WEIGHTS = {
    "requirement_match": ("需求匹配", 0.35),
    "model_consensus": ("模型共识", 0.25),
    "data_trust": ("数据可信", 0.20),
    "value": ("性价比", 0.15),
    "freshness": ("时效性", 0.05),
}


def _latest_prices(variant: PhoneVariant) -> tuple[float | None, dict[str, Any] | None, list[dict[str, Any]]]:
    query = quote(f"{variant.model.brand.name} {variant.model.model_name} {variant.variant_name} 官方旗舰店")
    search_urls = {
        "jd": f"https://search.jd.com/Search?keyword={query}",
        "tmall": f"https://list.tmall.com/search_product.htm?q={query}",
        "pdd": f"https://mobile.yangkeduo.com/search_result.html?search_key={query}",
    }
    rows: list[tuple[datetime, float, dict[str, Any]]] = []
    for listing in variant.listings:
        if not listing.is_active or not listing.store_verified:
            continue
        latest = latest_snapshot(listing, require_in_stock=True, require_public_price=True)
        if latest is None:
            continue
        price = latest.public_sale_price or latest.regular_price
        if price is None:
            continue
        rows.append(
            (
                latest.crawled_at,
                float(price),
                {
                    "platform": listing.platform,
                    "platform_name": PLATFORM_NAMES.get(listing.platform, listing.platform),
                    "store_name": listing.store_name,
                    "crawl_status": latest.crawl_status,
                    "is_reviewed": latest.crawl_status == "reviewed",
                    "has_evidence": bool(latest.evidence_text_path or latest.screenshot_path),
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
    rows.sort(key=lambda item: item[1])
    lowest_price, lowest_detail = rows[0][1], rows[0][2]
    by_platform: dict[str, tuple[datetime, float, dict[str, Any]]] = {}
    for row in rows:
        previous = by_platform.get(row[2]["platform"])
        if previous is None or row[1] < previous[1]:
            by_platform[row[2]["platform"]] = row
    platform_prices: list[dict[str, Any]] = []
    for key, name in PLATFORM_NAMES.items():
        row = by_platform.get(key)
        if row is None:
            platform_prices.append({"platform": key, "platform_name": name, "price": None, "url": None, "purchase_url": search_urls[key], "link_verified": False, "is_lowest": False, "freshness_label": "尚未核验", "is_stale": True})
            continue
        detail = row[2]
        url = detail["url"] if detail["url"] and "example.com" not in detail["url"] else None
        platform_prices.append({
            **detail,
            "price": row[1],
            "url": url,
            "purchase_url": url or search_urls[key],
            "link_verified": bool(url),
            "is_lowest": row[1] == lowest_price and detail["platform"] == lowest_detail["platform"],
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
        "weight_g": phone.weight_g,
        "waterproof": phone.waterproof,
        "operating_system": phone.operating_system,
        "source_url": phone.source_url,
        "source_checked_at": phone.source_checked_at.isoformat() if phone.source_checked_at else None,
        "data_quality": phone.data_quality,
    }


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
    candidates.sort(key=lambda item: (item["current_price"] is None, item["current_price"] or math.inf))
    affordable = [item for item in candidates if item["current_price"] is not None and item["current_price"] <= requirements.budget]
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
        lines.append(f"{len(model_values)} 个模型有效评分，平均 {statistics.mean(model_values):.1f}，分歧标准差 {deviation:.1f}。")
    else:
        lines.append("本次没有可解析模型评分，已使用规则分降级计算。")
    detail = candidate.get("price_detail")
    if detail:
        lines.append(f"最低价来自{detail['platform_name']}已核验商品页，{detail.get('freshness_label', '已记录采集时间')}。")
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
    if any(key in cpu for key in ("第五代骁龙", "8至尊", "8 至尊", "a19 pro", "天玑9500", "麒麟9030")):
        score = 97
    elif any(key in cpu for key in ("第三代骁龙8", "a19", "a18", "天玑9400", "天玑9300", "麒麟9020")):
        score = 91
    elif any(key in cpu for key in ("骁龙8", "天玑8550", "天玑8400", "麒麟")):
        score = 84
    elif any(key in cpu for key in ("骁龙7", "天玑8", "天玑7")):
        score = 73
    elif any(key in cpu for key in ("天玑6300", "骁龙6")):
        score = 58
    else:
        score = 62
    if any(key in model for key in ("iqoo", "gt", "ace", "k80")):
        score = max(score, 86)
    if any(key in model for key in ("ultra", "pro max")):
        score = max(score, 88)
    return score


def _series_camera_score(candidate: dict[str, Any]) -> float:
    model = candidate["model"].lower()
    if "ultra" in model or "pura" in model:
        return 95
    if any(key in model for key in ("find x", "x200 pro", "x300 pro", "magic8 pro", "iphone 17 pro")):
        return 91
    if any(key in model for key in ("mate", "iphone", "reno", "x200", "x300", "s26", "s25")):
        return 84
    if "pro" in model:
        return 78
    return 65


def _profile_scores(candidate: dict[str, Any]) -> dict[str, float]:
    camera_mp = candidate.get("main_camera_mp")
    camera_sensor = 55 if camera_mp is None else _clamp(58 + math.sqrt(min(float(camera_mp), 200) / 50) * 22)
    camera = 0.58 * _series_camera_score(candidate) + 0.30 * camera_sensor + 0.12 * _scale(candidate.get("storage_gb"), 128, 1024)
    gaming = (
        0.43 * _chipset_score(candidate)
        + 0.22 * _scale(candidate.get("refresh_rate"), 60, 165)
        + 0.16 * _scale(candidate.get("battery_mah"), 4000, 8000)
        + 0.11 * _scale(candidate.get("charging_w"), 18, 120)
        + 0.08 * _scale(candidate.get("ram_gb"), 6, 16)
    )
    weight_score = 55 if candidate.get("weight_g") is None else _clamp(100 - max(0, float(candidate["weight_g"]) - 170) * 1.35)
    endurance = (
        0.55 * _scale(candidate.get("battery_mah"), 4000, 8000)
        + 0.30 * weight_score
        + 0.15 * _scale(candidate.get("charging_w"), 18, 120)
    )
    balanced = 0.40 * gaming + 0.32 * camera + 0.28 * endurance
    return {"摄影创作": _clamp(camera), "重度游戏": _clamp(gaming), "轻薄续航": _clamp(endurance), "综合体验": _clamp(balanced)}


def _usage_score(candidate: dict[str, Any], usage: str) -> float:
    return _profile_scores(candidate).get(usage, _profile_scores(candidate)["综合体验"])


def _base_components(candidate: dict[str, Any], requirements: Requirements, all_prices: list[float]) -> dict[str, float]:
    price = candidate.get("current_price") or requirements.budget * 1.5
    budget_score = 100 if price <= requirements.budget else max(0, 100 - (price - requirements.budget) / requirements.budget * 160)
    brand_score = 100 if not requirements.brand or requirements.brand.lower() in candidate["brand"].lower() else 55
    usage_score = _usage_score(candidate, requirements.usage)
    # Storage is already a hard constraint. Meeting it should not be scored as
    # zero or allow a larger but otherwise weaker variant to dominate.
    storage_score = 100.0
    requirement = 0.82 * usage_score + 0.10 * budget_score + 0.08 * storage_score
    completeness_fields = ["cpu", "screen_size", "refresh_rate", "main_camera_mp", "battery_mah", "weight_g", "source_url"]
    completeness = sum(candidate.get(field) not in (None, "") for field in completeness_fields) / len(completeness_fields) * 100
    quality_bonus = {
        "official_verified": 10,
        "manual_official_review": 7,
        "official_partial": 2,
        "legacy_demo": -8,
        "demo": -8,
    }.get(candidate.get("data_quality"), 0)
    data_trust = max(0, min(100, completeness + quality_bonus))
    if len(all_prices) > 1 and max(all_prices) > min(all_prices):
        relative_price = 100 - (price - min(all_prices)) / (max(all_prices) - min(all_prices)) * 45
        # Value is quality-for-price, not simply "cheapest wins".  The usage
        # profile therefore has more influence than the raw relative price.
        value = 0.70 * usage_score + 0.30 * relative_price
    else:
        value = 0.70 * usage_score + 24
    freshness = 35.0
    detail = candidate.get("price_detail")
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
需求：预算{requirements.budget:g}元；用途{requirements.usage}；补充{requirements.details[:80] or '无'}
评分重点：{criteria}
候选手机：{json.dumps(compact_candidates, ensure_ascii=False)}
对全部5个候选分别给0到100分，每个id必须且只能出现一次。严格输出一个以候选id为键的对象：{{"scores":{{"候选id":分数}}}}
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


def _shortlist(candidates: list[dict[str, Any]], requirements: Requirements, limit: int = 5) -> list[dict[str, Any]]:
    prices = [item["current_price"] for item in candidates if item.get("current_price") is not None]
    ranked = []
    for item in candidates:
        components = _base_components(item, requirements, prices)
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
    price_source = "已核验平台公开价" if candidate.get("price_detail") else "公开发售价参考"
    margin = requirements.budget - float(candidate["current_price"])
    model_text = f"{len(model_values)} 个有效模型评分均值 {statistics.mean(model_values):.1f}、分歧标准差 {deviation:.1f}" if model_values else "模型超时后已由规则评分安全降级"
    caution = "购买前仍需在结算页确认实时价格和补贴资格" if candidate.get("price_detail") else "目前缺少已核验平台价，购买前请进入平台确认实时成交价"
    return f"第{rank}名推荐 {name}：" + "，".join(facts[:3]) + f"。{price_source}为 ¥{candidate['current_price']:,.0f}，在预算内余 ¥{margin:,.0f}；{model_text}。{caution}。"


def recommend(db: Session, requirements: Requirements) -> dict[str, Any]:
    started = time.perf_counter()
    candidates = load_candidates(db, requirements, limit=None)
    candidates = _shortlist(candidates, requirements, limit=5)
    run = RecommendationRun(
        user_query=requirements.details or f"预算{requirements.budget}元，{requirements.usage}",
        parsed_requirements=json.dumps(asdict(requirements), ensure_ascii=False),
        candidate_count=len(candidates),
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
    client = OllamaClient()
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
    prices = [item["current_price"] for item in candidates if item.get("current_price")]
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        components = _base_components(candidate, requirements, prices)
        model_items = scores_by_id.get(candidate["variant_id"], [])
        model_values = [item[0] for item in model_items]
        if model_values:
            average = statistics.mean(model_values)
            deviation = statistics.pstdev(model_values) if len(model_values) > 1 else 12
            agreement = max(0.7, 1 - deviation / 100)
            raw_consensus = average * agreement
            consensus = _clamp(raw_consensus, components["requirement_match"] - 8, components["requirement_match"] + 8)
        else:
            average, deviation, consensus = components["requirement_match"], 25.0, components["requirement_match"] * 0.7
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
                "model_deviation": round(deviation, 1),
                "confidence": "高" if len(model_values) >= 3 and deviation <= 10 else "中" if model_values else "低",
                "reason": "",
                "pros": pros,
                "cons": cons,
                "model_votes": len(model_values),
                "score_breakdown": score_breakdown,
                "evidence_lines": _evidence_lines(candidate, requirements, model_values, deviation),
            }
        )
    results.sort(key=lambda item: item["score"], reverse=True)
    results = results[:3]
    for index, item in enumerate(results, start=1):
        model_values = [score for score, _ in scores_by_id.get(item["variant_id"], [])]
        deviation = statistics.pstdev(model_values) if len(model_values) > 1 else (12 if model_values else 25)
        item["reason"] = _natural_explanation(item, requirements, index, model_values, deviation)
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
        "formula": "需求匹配35% + 模型共识25% + 数据可信度20% + 性价比15% + 时效性5%",
    }
