from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.market_import import _store_whitelist, _valid_product_url


def _external_id(platform: str, product_url: str) -> str:
    parsed = urlparse(product_url)
    if platform == "jd":
        match = re.search(r"/(?:product/)?([^/?#]+?)(?:\.html)?$", parsed.path)
        if match:
            return match.group(1)
    query_match = re.search(r"(?:^|[?&])(?:id|goods_id)=([^&#]+)", product_url)
    return query_match.group(1) if query_match else ""


def attach_candidate(
    path: Path,
    *,
    platform: str,
    model_name: str,
    ram_gb: str,
    storage_gb: str,
    product_url: str,
    store_name: str,
    source_note: str,
) -> dict[str, str]:
    platform = platform.strip().lower()
    product_url = product_url.strip()
    store_name = store_name.strip()
    if not _valid_product_url(platform, product_url):
        raise ValueError("商品链接域名与平台不匹配，且必须使用 HTTPS")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    required = {"brand", "model_name", "ram_gb", "storage_gb", "platform", "product_url", "review_status"}
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(f"价格队列缺少列：{', '.join(sorted(missing))}")

    matches = [
        row
        for row in rows
        if row.get("platform", "").strip().lower() == platform
        and row.get("model_name", "").strip() == model_name.strip()
        and row.get("ram_gb", "").strip() == ram_gb.strip()
        and row.get("storage_gb", "").strip() == storage_gb.strip()
    ]
    if len(matches) != 1:
        raise ValueError(f"候选键必须唯一，实际匹配 {len(matches)} 行")

    row = matches[0]
    store_key = (platform, row.get("brand", "").strip().casefold(), store_name.casefold())
    if store_key not in _store_whitelist():
        raise ValueError("店铺未进入官方店白名单，不能写入候选链接")

    row["product_url"] = product_url
    row["store_name"] = store_name
    row["external_id"] = _external_id(platform, product_url)
    row["capture_status"] = "needs_collection"
    row["capture_note"] = source_note.strip() or "已核对官方店与版本，尚未采集页面价格"
    row["review_status"] = "needs_collection"
    # Replacing a link invalidates any evidence and price fields from a previous
    # capture. They must be collected and reviewed again from the new page.
    for field in (
        "product_title", "regular_price", "public_sale_price", "evidence_text_path",
        "screenshot_path", "reviewer", "captured_at",
    ):
        row[field] = ""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
    return {
        "platform": platform,
        "model_name": model_name.strip(),
        "ram_gb": ram_gb.strip(),
        "storage_gb": storage_gb.strip(),
        "external_id": row["external_id"],
        "status": "needs_collection",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将已核对官方店和版本的商品链接安全写入价格采集队列")
    parser.add_argument("file", type=Path)
    parser.add_argument("--platform", required=True, choices=("jd", "tmall", "pdd"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--ram", required=True)
    parser.add_argument("--storage", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--source-note", default="")
    args = parser.parse_args()
    print(attach_candidate(
        args.file,
        platform=args.platform,
        model_name=args.model,
        ram_gb=args.ram,
        storage_gb=args.storage,
        product_url=args.url,
        store_name=args.store,
        source_note=args.source_note,
    ))


if __name__ == "__main__":
    main()
