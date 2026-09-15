import csv
import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "approve_price_row.py"
SPEC = importlib.util.spec_from_file_location("approve_price_row", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_approve_requires_matching_store_model_variant_and_price(tmp_path, monkeypatch) -> None:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("vivo手机官方旗舰店 iQOO 15 存储容量 16GB+256GB 券后 4319.1", encoding="utf-8")
    queue = tmp_path / "queue.csv"
    fields = [
        "brand", "model_name", "ram_gb", "storage_gb", "platform", "product_url", "store_name",
        "regular_price", "public_sale_price", "gov_price", "billion_subsidy_price",
        "evidence_text_path", "capture_status", "reviewer", "promotion_labels", "review_status",
    ]
    with queue.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "brand": "vivo", "model_name": "iQOO 15", "ram_gb": "16", "storage_gb": "256",
            "platform": "tmall", "product_url": "https://detail.tmall.com/item.htm?id=1",
            "store_name": "vivo手机官方旗舰店", "regular_price": "4899", "public_sale_price": "4319.1",
            "evidence_text_path": str(evidence), "capture_status": "needs_review", "review_status": "needs_review",
        })
    monkeypatch.setattr(MODULE, "_store_whitelist", lambda: {("tmall", "vivo", "vivo手机官方旗舰店")})
    result = MODULE.approve(
        queue, platform="tmall", model_name="iQOO 15", ram_gb="16", storage_gb="256",
        reviewer="测试审核人", promotion_labels="券后价",
    )
    assert result["status"] == "approved"
    with queue.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["review_status"] == "approved"
    assert row["reviewer"] == "测试审核人"
    assert row["promotion_labels"] == "券后价"
