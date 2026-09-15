from datetime import datetime, timezone

from app.models import PriceSnapshot
from app.services.pricing import freshness_metadata, split_promotion_labels


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


def test_promotion_labels_support_common_separators() -> None:
    assert split_promotion_labels("百亿补贴; 券后价，限时直降") == ["百亿补贴", "券后价", "限时直降"]
