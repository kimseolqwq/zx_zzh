from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from app.models import PlatformListing, PriceSnapshot


PRICE_STALE_DAYS = 7
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


def as_aware_utc(value: datetime) -> datetime:
    """Normalize SQLite's sometimes-naive datetimes before doing age math."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def latest_snapshot(
    listing: PlatformListing,
    *,
    require_in_stock: bool = False,
    require_public_price: bool = False,
) -> PriceSnapshot | None:
    snapshots: Iterable[PriceSnapshot] = listing.prices
    if require_in_stock:
        snapshots = (item for item in snapshots if item.in_stock)
    if require_public_price:
        snapshots = (item for item in snapshots if item.regular_price is not None or item.public_sale_price is not None)
    return max(snapshots, key=lambda item: as_aware_utc(item.crawled_at), default=None)


def freshness_metadata(snapshot: PriceSnapshot | None, *, now: datetime | None = None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "age_days": None,
            "freshness_label": "尚未核验",
            "is_stale": True,
            "checked_at": None,
            "checked_date": None,
        }
    current = as_aware_utc(now or datetime.now(timezone.utc))
    crawled = as_aware_utc(snapshot.crawled_at)
    age_days = max(0, int((current - crawled).total_seconds() // 86400))
    if age_days == 0:
        label = "今日核验"
    elif age_days <= PRICE_STALE_DAYS:
        label = f"{age_days}天前核验"
    else:
        label = f"已超过{PRICE_STALE_DAYS}天"
    local_time = crawled.astimezone(DISPLAY_TIMEZONE)
    return {
        "age_days": age_days,
        "freshness_label": label,
        "is_stale": age_days > PRICE_STALE_DAYS,
        "checked_at": local_time.strftime("%Y-%m-%d %H:%M"),
        "checked_date": local_time.strftime("%Y-%m-%d"),
    }


def snapshot_summary(snapshot: PriceSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            **freshness_metadata(None),
            "price": None,
            "crawl_status": None,
            "is_reviewed": False,
            "has_evidence": False,
            "in_stock": False,
        }
    return {
        **freshness_metadata(snapshot),
        "price": float(snapshot.public_sale_price or snapshot.regular_price) if (snapshot.public_sale_price or snapshot.regular_price) is not None else None,
        "crawl_status": snapshot.crawl_status,
        "is_reviewed": snapshot.crawl_status == "reviewed",
        "has_evidence": bool(snapshot.evidence_text_path or snapshot.screenshot_path),
        "in_stock": snapshot.in_stock,
    }
