from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.market_import import _money, _store_whitelist, _valid_product_url


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def approve(
    path: Path, *, platform: str, model_name: str, ram_gb: str, storage_gb: str,
    reviewer: str, promotion_labels: str,
) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    matches = [
        row for row in rows
        if row.get("platform", "").strip() == platform
        and row.get("model_name", "").strip() == model_name
        and row.get("ram_gb", "").strip() == ram_gb
        and row.get("storage_gb", "").strip() == storage_gb
    ]
    if len(matches) != 1:
        raise ValueError(f"审核键必须唯一，实际匹配 {len(matches)} 行")
    row = matches[0]
    if row.get("capture_status", "").strip().lower() == "blocked":
        raise ValueError("页面处于 blocked 状态，不能批准")
    if not reviewer.strip():
        raise ValueError("审核人不能为空")
    if not _valid_product_url(platform, row.get("product_url", "").strip()):
        raise ValueError("商品链接域名与平台不匹配")
    store_name = row.get("store_name", "").strip()
    store_key = (platform, row.get("brand", "").strip().casefold(), store_name.casefold())
    if store_key not in _store_whitelist():
        raise ValueError("店铺未进入官方店白名单")
    evidence_value = row.get("evidence_text_path", "").strip()
    evidence_path = Path(evidence_value)
    if not evidence_path.is_absolute():
        evidence_path = PROJECT_ROOT / evidence_path
    if not evidence_path.is_file():
        raise ValueError("正文证据文件不存在")
    evidence = evidence_path.read_text(encoding="utf-8")
    compact_evidence = _compact(evidence)
    if _compact(store_name) not in compact_evidence:
        raise ValueError("证据正文不包含精确店铺名称")
    if _compact(model_name) not in compact_evidence:
        raise ValueError("证据正文不包含目标型号")
    storage_labels = {f"{ram_gb}gb+{storage_gb}gb"}
    if storage_gb == "1024":
        storage_labels.add(f"{ram_gb}gb+1tb")
    if ram_gb and not any(_compact(label) in compact_evidence for label in storage_labels):
        raise ValueError("证据正文不包含目标内存版本")
    prices = [_money(row, key) for key in ("regular_price", "public_sale_price", "gov_price", "billion_subsidy_price")]
    if not any(price is not None for price in prices):
        raise ValueError("没有可审核价格")
    row["reviewer"] = reviewer.strip()
    row["promotion_labels"] = promotion_labels.strip() or row.get("promotion_labels", "")
    row["review_status"] = "approved"
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
    return {
        "platform": platform, "model_name": model_name, "ram_gb": ram_gb,
        "storage_gb": storage_gb, "reviewer": reviewer.strip(), "status": "approved",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="对证据、白名单、型号和版本均一致的单条价格进行审核批准")
    parser.add_argument("file", type=Path)
    parser.add_argument("--platform", required=True, choices=("jd", "tmall", "pdd"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--ram", required=True)
    parser.add_argument("--storage", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--promotion-labels", default="人工复核公开价格")
    args = parser.parse_args()
    print(approve(
        args.file, platform=args.platform, model_name=args.model, ram_gb=args.ram,
        storage_gb=args.storage, reviewer=args.reviewer, promotion_labels=args.promotion_labels,
    ))


if __name__ == "__main__":
    main()
