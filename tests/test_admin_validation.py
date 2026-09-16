from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers.admin import _validate_listing_input, _validate_price_relationships


def test_listing_input_accepts_exact_whitelisted_store() -> None:
    _validate_listing_input(
        brand_name="vivo",
        platform="jd",
        store_name="iQOO京东自营旗舰店",
        product_url="https://item.jd.com/100151088622.html",
    )


@pytest.mark.parametrize(
    ("platform", "store", "url"),
    [
        ("jd", "某第三方手机店", "https://item.jd.com/1.html"),
        ("jd", "iQOO京东自营旗舰店", "https://detail.tmall.com/item.htm?id=1"),
        ("other", "iQOO京东自营旗舰店", "https://item.jd.com/1.html"),
    ],
)
def test_listing_input_rejects_untrusted_combinations(platform: str, store: str, url: str) -> None:
    with pytest.raises(HTTPException):
        _validate_listing_input(brand_name="vivo", platform=platform, store_name=store, product_url=url)


def test_price_relationships_reject_impossible_discount_price() -> None:
    with pytest.raises(HTTPException, match="国补价"):
        _validate_price_relationships(
            Decimal("4999"), Decimal("4599"), Decimal("4699"), None, None,
        )


def test_price_relationships_accept_partial_snapshot() -> None:
    _validate_price_relationships(None, None, Decimal("3999"), None, None)
