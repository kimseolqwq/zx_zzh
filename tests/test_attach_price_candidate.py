import csv
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "attach_price_candidate.py"
SPEC = importlib.util.spec_from_file_location("attach_price_candidate", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _queue(path: Path) -> None:
    fields = [
        "brand", "model_name", "ram_gb", "storage_gb", "platform", "product_url",
        "store_name", "external_id", "product_title", "regular_price", "public_sale_price",
        "gov_price", "billion_subsidy_price", "promotion_labels", "evidence_text_path",
        "screenshot_path", "reviewer", "review_status", "capture_status", "capture_note",
        "captured_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "brand": "vivo", "model_name": "iQOO 13", "ram_gb": "16", "storage_gb": "256",
            "platform": "jd", "review_status": "pending", "regular_price": "3999",
            "evidence_text_path": "stale.txt",
        })


def test_attach_candidate_sets_link_without_approving_price(tmp_path: Path) -> None:
    path = tmp_path / "queue.csv"
    _queue(path)
    result = MODULE.attach_candidate(
        path,
        platform="jd",
        model_name="iQOO 13",
        ram_gb="16",
        storage_gb="256",
        product_url="https://item.jd.com/100151088622.html",
        store_name="iQOO京东自营旗舰店",
        source_note="公开索引核对型号、版本与店铺；价格待商品页采集",
    )
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert result["external_id"] == "100151088622"
    assert row["review_status"] == "needs_collection"
    assert row["capture_status"] == "needs_collection"
    assert row["regular_price"] == ""
    assert row["evidence_text_path"] == ""


def test_attach_candidate_rejects_non_whitelisted_store(tmp_path: Path) -> None:
    path = tmp_path / "queue.csv"
    _queue(path)
    with pytest.raises(ValueError, match="白名单"):
        MODULE.attach_candidate(
            path,
            platform="jd",
            model_name="iQOO 13",
            ram_gb="16",
            storage_gb="256",
            product_url="https://item.jd.com/100151088622.html",
            store_name="某第三方手机店",
            source_note="",
        )
