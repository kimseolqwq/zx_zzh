from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Brand,
    ModelRun,
    PhoneModel,
    PhoneVariant,
    PlatformListing,
    PriceSnapshot,
    RecommendationRun,
)
from app.services.pricing import as_aware_utc, latest_snapshot, snapshot_summary
from app.services.recommendation import SCORE_WEIGHTS


PLACEHOLDER_VALUES = {"", "—", "未知", "待补充", "CPU型号", "移动平台", "官方参数正在补充"}


def is_meaningful(value: Any) -> bool:
    return value is not None and str(value).strip() not in PLACEHOLDER_VALUES


def _percent(count: int, total: int) -> float:
    return round(count / total * 100, 1) if total else 0.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 1)


def build_evaluation_metrics(db: Session) -> dict[str, Any]:
    phones = db.scalars(
        select(PhoneModel)
        .options(selectinload(PhoneModel.variants))
        .where(PhoneModel.is_active.is_(True), PhoneModel.sale_status.in_(("on_sale", "partial_sale")))
    ).unique().all()
    variants = db.scalars(
        select(PhoneVariant).where(PhoneVariant.is_active.is_(True))
    ).all()
    listings = db.scalars(
        select(PlatformListing)
        .options(selectinload(PlatformListing.prices))
        .where(PlatformListing.is_active.is_(True))
    ).unique().all()

    fields = [
        ("cpu", "处理器", lambda phone: phone.cpu),
        ("screen", "屏幕尺寸", lambda phone: phone.screen_size),
        ("refresh", "刷新率", lambda phone: phone.refresh_rate),
        ("camera", "主摄像素", lambda phone: phone.main_camera_mp),
        ("battery", "电池容量", lambda phone: phone.battery_mah),
        ("weight", "机身重量", lambda phone: phone.weight_g),
        ("image", "产品图", lambda phone: phone.image_url),
    ]
    field_coverage = []
    for key, label, getter in fields:
        count = sum(is_meaningful(getter(phone)) for phone in phones)
        field_coverage.append({"key": key, "label": label, "count": count, "total": len(phones), "percent": _percent(count, len(phones))})

    platform_counts: dict[str, dict[str, int]] = {
        key: {"listings": 0, "verified": 0, "manual": 0, "priced": 0, "fresh": 0}
        for key in ("jd", "tmall", "pdd")
    }
    priced_count = fresh_count = evidence_count = 0
    verified_listing_count = manual_listing_count = verified_fresh_count = 0
    for listing in listings:
        row = platform_counts.setdefault(
            listing.platform,
            {"listings": 0, "verified": 0, "manual": 0, "priced": 0, "fresh": 0},
        )
        row["listings"] += 1
        reviewed = [
            item for item in listing.prices
            if item.crawl_status == "reviewed" and (item.evidence_text_path or item.screenshot_path)
        ]
        manual = [
            item for item in listing.prices
            if item.crawl_status in {"manual", "manual_unavailable", "manual_not_found"}
        ]
        if reviewed and listing.store_verified:
            verified_listing_count += 1
            row["verified"] += 1
        elif manual:
            manual_listing_count += 1
            row["manual"] += 1
        latest = snapshot_summary(latest_snapshot(listing))
        if latest["price"] is not None:
            priced_count += 1
            row["priced"] += 1
            if not latest["is_stale"]:
                fresh_count += 1
                row["fresh"] += 1
        if reviewed:
            reviewed_latest = max(reviewed, key=lambda item: as_aware_utc(item.crawled_at))
            if not snapshot_summary(reviewed_latest)["is_stale"]:
                verified_fresh_count += 1
        if latest["has_evidence"] and latest["is_reviewed"]:
            evidence_count += 1

    recent_runs = db.scalars(select(RecommendationRun).order_by(RecommendationRun.created_at.desc()).limit(100)).all()
    successful_runs = [row for row in recent_runs if row.success]
    run_latencies = [row.total_latency_ms for row in successful_runs if row.total_latency_ms is not None]
    recent_model_runs = db.scalars(select(ModelRun).order_by(ModelRun.created_at.desc()).limit(300)).all()
    grouped: dict[str, list[ModelRun]] = defaultdict(list)
    for row in recent_model_runs:
        grouped[row.model_name].append(row)
    model_metrics = []
    for model_name, rows in grouped.items():
        latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
        speeds = [row.tokens_per_second for row in rows if row.tokens_per_second is not None]
        model_metrics.append({
            "model_name": model_name,
            "calls": len(rows),
            "success_rate": _percent(sum(row.success for row in rows), len(rows)),
            "json_rate": _percent(sum(row.json_parse_success for row in rows), len(rows)),
            "avg_latency_s": round(statistics.mean(latencies) / 1000, 2) if latencies else None,
            "p95_latency_s": round((_percentile(latencies, 0.95) or 0) / 1000, 2) if latencies else None,
            "avg_tps": round(statistics.mean(speeds), 2) if speeds else None,
        })
    configured_order = {name: index for index, name in enumerate(("qwen3:8b", "deepseek-r1:7b", "gemma3:4b"))}
    model_metrics.sort(key=lambda item: configured_order.get(item["model_name"], 99))

    source_traceable = sum(bool(phone.source_url or phone.official_url) for phone in phones)
    phones_with_variants = sum(any(item.is_active for item in phone.variants) for phone in phones)
    launch_prices = sum(item.launch_price is not None for item in variants)
    return {
        "dataset": {
            "brands": db.scalar(select(func.count()).select_from(Brand).where(Brand.is_active.is_(True))) or 0,
            "phones": len(phones),
            "variants": len(variants),
            "source_traceable": source_traceable,
            "source_traceable_rate": _percent(source_traceable, len(phones)),
            "phones_with_variants": phones_with_variants,
            "variant_coverage_rate": _percent(phones_with_variants, len(phones)),
            "launch_prices": launch_prices,
            "launch_price_rate": _percent(launch_prices, len(variants)),
            "reviewed_snapshots": db.scalar(select(func.count()).select_from(PriceSnapshot).where(PriceSnapshot.crawl_status == "reviewed")) or 0,
        },
        "field_coverage": field_coverage,
        "prices": {
            "all_listings": len(listings),
            "verified_listings": verified_listing_count,
            "manual_listings": manual_listing_count,
            "priced": priced_count,
            "fresh": fresh_count,
            "verified_fresh": verified_fresh_count,
            "evidence": evidence_count,
            "fresh_rate": _percent(fresh_count, priced_count),
            "evidence_rate": _percent(evidence_count, len(listings)),
            "platforms": [
                {"key": key, "name": {"jd": "京东", "tmall": "天猫", "pdd": "拼多多"}.get(key, key), **values}
                for key, values in platform_counts.items()
            ],
        },
        "runs": {
            "count": len(recent_runs),
            "success_rate": _percent(len(successful_runs), len(recent_runs)),
            "p50_latency_s": round((_percentile(run_latencies, 0.50) or 0) / 1000, 2) if run_latencies else None,
            "p95_latency_s": round((_percentile(run_latencies, 0.95) or 0) / 1000, 2) if run_latencies else None,
        },
        "models": model_metrics,
        "score_weights": [
            {"key": key, "label": label, "weight": int(weight * 100)}
            for key, (label, weight) in SCORE_WEIGHTS.items()
        ],
    }
