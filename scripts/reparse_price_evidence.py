from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.crawlers.price_parser import parse_price_text


BLOCKED_WORDS = (
    "验证码", "滑块", "登录后查看", "访问过于频繁", "访问频繁", "无法搜索",
    "安全验证", "暂时无法展示该商品的信息",
)


def reparse(path: Path) -> dict[str, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for field in ("capture_status", "capture_note"):
        if field not in fieldnames:
            fieldnames.append(field)
    counters = {"evidence_rows": 0, "with_prices": 0, "blocked": 0, "missing_evidence": 0}
    for row in rows:
        evidence_value = row.get("evidence_text_path", "").strip()
        if not evidence_value:
            continue
        evidence = Path(evidence_value)
        if not evidence.is_absolute():
            evidence = PROJECT_ROOT / evidence
        if not evidence.is_file():
            counters["missing_evidence"] += 1
            continue
        counters["evidence_rows"] += 1
        text = evidence.read_text(encoding="utf-8")
        parsed = parse_price_text(text)
        blocked = next((word for word in BLOCKED_WORDS if word in text), None)
        values = (parsed.regular_price, parsed.public_sale_price)
        if blocked or ("你好，请登录" in text and not any(value is not None for value in values)):
            row["capture_status"] = "blocked"
            row["capture_note"] = f"页面要求人工处理：{blocked or '未登录或商品内容未加载'}"
            row["review_status"] = "blocked"
            counters["blocked"] += 1
            continue
        row["regular_price"] = parsed.regular_price or ""
        row["public_sale_price"] = parsed.public_sale_price or ""
        row["capture_note"] = f"离线重解析置信度：{parsed.confidence}"
        row["review_status"] = "needs_review"
        if any(value is not None for value in values):
            counters["with_prices"] += 1
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
    return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="使用最新保守规则离线重解析已保存的电商页面正文")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    print(reparse(args.file))


if __name__ == "__main__":
    main()
