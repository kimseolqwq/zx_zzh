from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.browser_prices import capture


CAPTURE_FIELDS = ("capture_status", "capture_note", "captured_at")


def _portable_path(value: str) -> str:
    if not value:
        return ""
    candidate = Path(value)
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return value


def _save(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    for row in rows:
        for field in ("evidence_text_path", "screenshot_path"):
            row[field] = _portable_path(row.get(field, ""))
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _safe_item_id(parts: tuple[str, ...]) -> str:
    joined = "-".join(part.strip().replace(" ", "_") for part in parts if part.strip())
    return re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", joined)[:160] or "market-item"


def process_queue(
    path: Path,
    *,
    limit: int,
    platform: str | None,
    headless: bool,
    model_name: str | None = None,
    ram_gb: str | None = None,
    storage_gb: str | None = None,
) -> dict[str, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    required = {"brand", "model_name", "storage_gb", "platform", "product_url", "review_status"}
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(f"价格队列缺少列：{', '.join(sorted(missing))}")
    for field in CAPTURE_FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)

    counters = {"attempted": 0, "captured": 0, "blocked": 0, "skipped": 0, "failed": 0}
    for index, row in enumerate(rows):
        if counters["attempted"] >= limit:
            break
        if platform and row.get("platform", "").strip().lower() != platform:
            continue
        if model_name and row.get("model_name", "").strip() != model_name:
            continue
        if ram_gb and row.get("ram_gb", "").strip() != ram_gb:
            continue
        if storage_gb and row.get("storage_gb", "").strip() != storage_gb:
            continue
        if row.get("review_status", "").strip().lower() not in {"pending", "needs_collection", "blocked", "existing_reviewed"}:
            counters["skipped"] += 1
            continue
        product_url = row.get("product_url", "").strip()
        if not product_url:
            counters["skipped"] += 1
            continue
        item_id = _safe_item_id(
            (
                row.get("platform", ""), row.get("brand", ""), row.get("model_name", ""),
                row.get("ram_gb", ""), row.get("storage_gb", ""),
            )
        )
        counters["attempted"] += 1
        try:
            result = capture(product_url, item_id, headless=headless)
            parsed = result["parsed"]
            row["product_title"] = row.get("product_title", "") or result.get("title", "")
            row["regular_price"] = parsed.get("regular_price") or ""
            row["public_sale_price"] = parsed.get("public_sale_price") or ""
            row["gov_price"] = parsed.get("displayed_gov_price") or ""
            row["billion_subsidy_price"] = parsed.get("billion_subsidy_price") or ""
            row["evidence_text_path"] = result.get("text_path", "")
            row["screenshot_path"] = result.get("screenshot_path", "")
            row["captured_at"] = result.get("captured_at", "")
            row["capture_status"] = result.get("status", "needs_review")
            row["capture_note"] = result.get("blocked_reason", "") or f"解析置信度：{parsed.get('confidence', 'low')}"
            row["review_status"] = "blocked" if result.get("status") == "blocked" else "needs_review"
            counters["blocked" if result.get("status") == "blocked" else "captured"] += 1
        except Exception as exc:
            row["capture_status"] = "failed"
            row["capture_note"] = str(exc)[:500]
            counters["failed"] += 1
        finally:
            # Save after every page so an interruption or captcha never loses
            # evidence already collected earlier in the run.
            rows[index] = row
            _save(path, rows, fieldnames)
    # Also migrates legacy absolute evidence paths when filters match no rows.
    _save(path, rows, fieldnames)
    return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="按审核队列低频采集商品页，并回填价格候选和证据路径")
    parser.add_argument("file", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--platform", choices=("jd", "tmall", "pdd"))
    parser.add_argument("--model", help="只处理指定型号")
    parser.add_argument("--ram", help="只处理指定运行内存（GB）")
    parser.add_argument("--storage", help="只处理指定存储容量（GB）")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须大于 0")
    result = process_queue(
        args.file,
        limit=args.limit,
        platform=args.platform,
        headless=args.headless,
        model_name=args.model,
        ram_gb=args.ram,
        storage_gb=args.storage,
    )
    print(result)


if __name__ == "__main__":
    main()
