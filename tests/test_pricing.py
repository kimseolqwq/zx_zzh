from datetime import datetime, timezone

from app.models import PriceSnapshot
from decimal import Decimal

from app.services.pricing import freshness_metadata, snapshot_summary


def test_price_freshness_labels_and_stale_threshold() -> None:
    snapshot = PriceSnapshot(listing_id=1, crawled_at=datetime(2026, 9, 1, 2, 30, tzinfo=timezone.utc))
    recent = freshness_metadata(snapshot, now=datetime(2026, 9, 3, 2, 29, tzinfo=timezone.utc))
    assert recent["age_days"] == 1
    assert recent["freshness_label"] == "1天前核验"
    assert recent["is_stale"] is False
    assert recent["checked_at"] == "2026-09-01 10:30"

    stale = freshness_metadata(snapshot, now=datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc))
    assert stale["is_stale"] is True
    assert stale["freshness_label"] == "已超过7天"


def test_snapshot_summary_only_exposes_public_price() -> None:
    snapshot = PriceSnapshot(
        listing_id=1, regular_price=Decimal("4299"), public_sale_price=Decimal("3999"),
        displayed_gov_price=Decimal("3499"), billion_subsidy_price=Decimal("3599"),
        crawled_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    summary = snapshot_summary(snapshot)
    assert summary["price"] == 3999
    assert not any("gov" in key or "subsidy" in key for key in summary)
