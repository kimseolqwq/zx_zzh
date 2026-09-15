import csv
import importlib.util
from datetime import date
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_price_queue.py"
SPEC = importlib.util.spec_from_file_location("generate_price_queue", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_existing_queue_rows_are_keyed_for_non_destructive_regeneration(tmp_path) -> None:
    path = tmp_path / "queue.csv"
    fields = ["brand", "model_name", "ram_gb", "storage_gb", "platform", "product_url", "review_status"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "brand": "小米", "model_name": "测试机", "ram_gb": "12", "storage_gb": "256",
            "platform": "jd", "product_url": "https://item.jd.com/1.html", "review_status": "needs_review",
        })
    rows = MODULE._existing_rows(path)
    preserved = rows[("小米", "测试机", "12", "256", "jd")]
    assert preserved["product_url"] == "https://item.jd.com/1.html"
    assert preserved["review_status"] == "needs_review"


def test_release_sort_value_places_newer_dates_first() -> None:
    values = ["2025-01-01", "", "2026-01-01"]
    assert sorted(values, key=MODULE._release_sort_value) == ["2026-01-01", "2025-01-01", ""]
    assert MODULE._release_sort_value(date(2026, 1, 1).isoformat()) < 0
