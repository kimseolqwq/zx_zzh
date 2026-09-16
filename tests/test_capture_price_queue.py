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
        "product_title", "regular_price", "public_sale_price",
        "evidence_text_path", "screenshot_path", "review_status",
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
                "confidence": "high",
            },
        }

    monkeypatch.setattr(MODULE, "capture", fake_capture)
    result = MODULE.process_queue(queue, limit=1, platform="jd", headless=True)
    assert result == {"attempted": 1, "captured": 1, "blocked": 0, "skipped": 0, "failed": 0}
    with queue.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["review_status"] == "needs_review"
    assert row["public_sale_price"] == "3999.0"
    assert row["capture_status"] == "success"


def test_process_queue_can_target_one_variant(tmp_path, monkeypatch) -> None:
    queue = tmp_path / "queue.csv"
    fields = [
        "brand", "model_name", "ram_gb", "storage_gb", "platform", "product_url",
        "product_title", "regular_price", "public_sale_price",
        "evidence_text_path", "screenshot_path", "review_status",
    ]
    with queue.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for storage in ("256", "512"):
            writer.writerow({
                "brand": "vivo", "model_name": "iQOO 13", "ram_gb": "16",
                "storage_gb": storage, "platform": "jd",
                "product_url": f"https://item.jd.com/{storage}.html", "review_status": "needs_collection",
            })

    seen = []

    def fake_capture(url: str, item_id: str, *, headless: bool) -> dict:
        seen.append(url)
        return {
            "title": "", "captured_at": "20260916T000000Z", "status": "blocked",
            "blocked_reason": "访问频繁", "text_path": "", "screenshot_path": "",
            "parsed": {"regular_price": None, "public_sale_price": None, "confidence": "low"},
        }

    monkeypatch.setattr(MODULE, "capture", fake_capture)
    result = MODULE.process_queue(
        queue, limit=5, platform="jd", headless=True,
        model_name="iQOO 13", ram_gb="16", storage_gb="512",
    )
    assert result["attempted"] == 1
    assert seen == ["https://item.jd.com/512.html"]


def test_portable_path_makes_project_evidence_relative() -> None:
    value = str(MODULE.PROJECT_ROOT / "data" / "raw" / "ecommerce" / "proof.txt")
    assert MODULE._portable_path(value) == "data/raw/ecommerce/proof.txt"
