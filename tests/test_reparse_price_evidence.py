import csv
import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reparse_price_evidence.py"
SPEC = importlib.util.spec_from_file_location("reparse_price_evidence", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_reparse_updates_explicit_prices_and_marks_login_page_blocked(tmp_path) -> None:
    sale = tmp_path / "sale.txt"
    blocked = tmp_path / "blocked.txt"
    sale.write_text("券后\n￥\n4319.1\n优惠前￥4899", encoding="utf-8")
    blocked.write_text("你好，请登录\n页面内容未加载", encoding="utf-8")
    queue = tmp_path / "queue.csv"
    fields = [
        "evidence_text_path", "regular_price", "public_sale_price", "gov_price",
        "billion_subsidy_price", "review_status",
    ]
    with queue.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"evidence_text_path": str(sale), "review_status": "needs_review"})
        writer.writerow({"evidence_text_path": str(blocked), "review_status": "needs_review"})

    result = MODULE.reparse(queue)
    assert result == {"evidence_rows": 2, "with_prices": 1, "blocked": 1, "missing_evidence": 0}
    with queue.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["regular_price"] == "4899.0"
    assert rows[0]["public_sale_price"] == "4319.1"
    assert rows[0]["review_status"] == "needs_review"
    assert rows[1]["review_status"] == "blocked"
