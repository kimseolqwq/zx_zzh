import csv
import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "capture_price_queue.py"
SPEC = importlib.util.spec_from_file_location("capture_price_queue", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_process_queue_backfills_capture_without_auto_approval(tmp_path, monkeypatch) -> None:
    queue = tmp_path / "queue.csv"
    fields = [
        "brand", "model_name", "ram_gb", "storage_gb", "platform", "product_url",
        "product_title", "regular_price", "public_sale_price", "gov_price",
        "billion_subsidy_price", "evidence_text_path", "screenshot_path", "review_status",
    ]
    with queue.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "brand": "测试", "model_name": "Phone / Pro", "ram_gb": "12", "storage_gb": "256",
            "platform": "jd", "product_url": "https://item.jd.com/1.html", "review_status": "pending",
        })

    def fake_capture(url: str, item_id: str, *, headless: bool) -> dict:
        assert "/" not in item_id
        return {
            "title": "测试商品", "captured_at": "20260916T000000Z", "status": "success",
            "blocked_reason": None, "text_path": "data/raw/ecommerce/a.txt",
            "screenshot_path": "data/raw/ecommerce/screenshots/a.png",
            "parsed": {
                "regular_price": 4299.0, "public_sale_price": 3999.0,
                "displayed_gov_price": 3599.0, "billion_subsidy_price": None,
                "confidence": "high",
            },
        }

    monkeypatch.setattr(MODULE, "capture", fake_capture)
    result = MODULE.process_queue(queue, limit=1, platform="jd", headless=True)
    assert result == {"attempted": 1, "captured": 1, "blocked": 0, "skipped": 0, "failed": 0}
    with queue.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["review_status"] == "needs_review"
    assert row["gov_price"] == "3599.0"
    assert row["capture_status"] == "success"
